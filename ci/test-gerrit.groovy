#!groovy

/// file: test-gerrit.groovy

def main() {
    def image_name = "python-curl-poetry";
    def dockerfile = "ci/Dockerfile";
    def docker_args = "${mount_reference_repo_dir}";

    if ("${env.GERRIT_EVENT_TYPE}" == "change-merged") {
        print("This is a merged change event run");
    }

    currentBuild.description += (
        """
        |Reason:            ${env.GERRIT_EVENT_TYPE}<br>
        |Commit SHA:        ${env.GERRIT_PATCHSET_REVISION}<br>
        |Patchset Number:   ${env.GERRIT_PATCHSET_NUMBER}<br>
        |""".stripMargin());

    dir("${checkout_dir}") {
        def docker_image = docker.build(image_name, "-f ${dockerfile} .");

        stage('Pre-commit hooks') {
            docker_image.inside(docker_args) {
                sh(label: "Pre-commit and run hooks", script: """
                    dev/run-in-venv \
                        pre-commit run --all-files
                """);
            }
        }

        stage("Validate entrypoints") {
            docker_image.inside(docker_args) {
                sh(label: "run entrypoints", script: """
                    set -o pipefail
                    poetry --version

                    poetry run activity-from-fs --help
                    poetry run binreplace --help
                    poetry run check-rpath --help
                    poetry run ci-artifacts --help
                    poetry run cmk-dev --help
                    poetry run cpumon --help
                    poetry run last-access --help

                    # scripts without argparser or some other reason
                    # poetry run decent-output --help
                    # poetry run procmon --help
                    # poetry run pycinfo --help
                """);
            }
        }

        stage("Create changelog and tag") {
            docker_image.inside(docker_args) {
                sh(label: "create changelog", script: """
                    set -o pipefail
                    dev/run-in-venv \
                        changelog-generator \
                        changelog changelog.md \
                        --snippets=.snippets \
                        --in-place \
                        --version-reference="https://review.lan.tribe29.com/gitweb?p=checkmk_dev_tools.git;a=tag;h=refs/tags/"
                """);

                sh(label: "parse changelog", returnStdout: true, script: """
                    dev/run-in-venv \
                        changelog2version \
                        --changelog_file changelog.md \
                        --version_file cmk_dev/version.py \
                        --version_file_type py \
                        --additional_version_info="-rc${env.GERRIT_PATCHSET_NUMBER}" \
                        --print \
                        --output version.json
                """);

                withCredentials([sshUserPrivateKey(credentialsId: "release-checkmk", keyFileVariable: 'keyfile')]) {
                    withEnv(["GIT_SSH_COMMAND=ssh -o \"StrictHostKeyChecking no\" -i ${keyfile} -l release"]) {
                        sh(label: "create and publish tag", returnStdout: true, script: """
                            cat version.json
                            CHANGELOG_VERSION=\$(jq -r .info.version version.json)
                            echo "Changelog version is: \$CHANGELOG_VERSION"

                            # this has to match with release and be added as "Forge Committer Identity"
                            # in the repo config with user "user/Check_MK release system (release)"
                            export GIT_AUTHOR_NAME="Checkmk release system"
                            export GIT_AUTHOR_EMAIL="noreply@checkmk.com"
                            export GIT_COMMITTER_NAME="Checkmk release system"
                            export GIT_COMMITTER_EMAIL="noreply@checkmk.com"
                            git fetch --prune --prune-tags
                            git tag -a v\$CHANGELOG_VERSION-rc${env.GERRIT_PATCHSET_NUMBER} -m "v\$CHANGELOG_VERSION-rc${env.GERRIT_PATCHSET_NUMBER}"
                            git tag --list
                            git push origin tag v\$CHANGELOG_VERSION-rc${env.GERRIT_PATCHSET_NUMBER}
                        """);
                    }
                }
            }
        }

        stage("Build and publish test package") {
            docker_image.inside(docker_args) {
                sh(label: "build package", script: """
                    # see comment in pyproject.toml
                    poetry self add "poetry-dynamic-versioning[plugin]"
                    rm -rf dist/*
                    poetry build
                    poetry run twine check dist/*
                    python3 -m pip uninstall -y checkmk_dev_tools
                    python3 -m pip install --pre --user dist/checkmk_dev_tools-*-py3-none-any.whl
                """);

                withCredentials([
                    string(credentialsId: 'TEST_PYPI_API_TOKEN_CMK_DEV_TOOLS_ONLY', variable: 'TEST_PYPI_API_TOKEN_CMK_DEV_TOOLS_ONLY')
                ]) {
                    sh(label: "publish package", script: """
                        poetry config repositories.testpypi https://test.pypi.org/legacy/
                        poetry config pypi-token.testpypi "${TEST_PYPI_API_TOKEN_CMK_DEV_TOOLS_ONLY}"
                        poetry publish --repository testpypi --skip-existing
                    """);
                }
            }
        }

        stage("Archive stuff") {
            show_duration("archiveArtifacts") {
                archiveArtifacts(
                    allowEmptyArchive: true,
                    artifacts: "dist/checkmk_dev_tools-*, changelog.md",
                    fingerprint: true,
                );
            }
        }
    }
}

return this;
