#!groovy

/// file: release.groovy

def main() {
    def image_name = "python-curl-poetry";
    def dockerfile = "ci/Dockerfile";
    def docker_args = "${mount_reference_repo_dir}";
    def release_new_version_flag = false;

    currentBuild.description += (
        """
        |Create an automatic release<br>
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

        stage("Create changelog") {
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
                        --print \
                        --output version.json
                """);
            }
        }

        stage("Check tag exists") {
            withCredentials([sshUserPrivateKey(credentialsId: "release-checkmk", keyFileVariable: 'keyfile')]) {
                withEnv(["GIT_SSH_COMMAND=ssh -o \"StrictHostKeyChecking no\" -i ${keyfile} -l release"]) {
                    release_new_version_flag = sh(script: """
                        git fetch --prune --prune-tags
                        CHANGELOG_VERSION=\$(jq -r .info.version version.json)
                        if [ \$(git tag -l "v\$CHANGELOG_VERSION") ]; then
                            echo "Tag v\$CHANGELOG_VERSION exits already"
                            exit 1
                        else
                            echo "Tag v\$CHANGELOG_VERSION does not yet exit"
                        fi
                    """, returnStatus: true) == 0;
                }
            }
        }

        smart_stage(name: "Create tag", condition: release_new_version_flag, raiseOnError: false) {
            docker_image.inside(docker_args) {
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
                            git tag -a v\$CHANGELOG_VERSION -m "v\$CHANGELOG_VERSION"
                            git tag --list
                            git push origin tag v\$CHANGELOG_VERSION
                        """);
                    }
                }
            }
        }

        stage("Build package") {
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
            }
        }

        smart_stage(name: "Publish package", condition: release_new_version_flag, raiseOnError: false) {
            docker_image.inside(docker_args) {
                withCredentials([
                    string(credentialsId: 'PYPI_API_TOKEN_CMK_DEV_TOOLS_ONLY', variable: 'PYPI_API_TOKEN_CMK_DEV_TOOLS_ONLY')
                ]) {
                    sh(label: "publish package", script: """
                        poetry config pypi-token.pypi "${PYPI_API_TOKEN_CMK_DEV_TOOLS_ONLY}"
                        poetry publish --skip-existing
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
