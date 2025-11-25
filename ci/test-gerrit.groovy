#!groovy

/// file: test-gerrit.groovy

def main() {
    def image_name = "python-curl-poetry";
    def dockerfile = "ci/Dockerfile";
    def docker_args = "${mount_reference_repo_dir}";
    def release_new_version_flag = false;

    if ("${env.GERRIT_EVENT_TYPE}" == "change-merged") {
        print("This is a merged change event run");
    }

    currentBuild.description += (
        """
        |Reason:            ${env.GERRIT_EVENT_TYPE}<br>
        |Commit SHA:        ${env.GERRIT_PATCHSET_REVISION}<br>
        |Patchset Number:   ${env.GERRIT_PATCHSET_NUMBER}<br>
        |Change Number:     ${env.GERRIT_CHANGE_NUMBER}<br>
        |""".stripMargin());

    dir("${checkout_dir}") {
        def docker_image = docker.build(image_name, "-f ${dockerfile} .");
        docker_image.inside(docker_args) {
            withCredentials([
                usernamePassword(
                    credentialsId: 'jenkins-api-token',
                    usernameVariable: 'JENKINS_USERNAME',
                    passwordVariable: 'JENKINS_PASSWORD',
                ),
                string(
                    credentialsId: 'INFLUXDB-Jenkins-read-official',
                    variable: 'INFLUXDB_READ_TOKEN',
                ),
            ]) {
                stage('Pre-commit hooks') {
                    sh(label: "Pre-commit and run hooks", script: """
                        dev/run-in-venv \
                            pre-commit run --all-files
                    """);
                }
            }

            stage("Validate entrypoints") {
                sh(label: "run entrypoints", script: """
                    set -o pipefail
                    poetry --version

                    poetry run binreplace --help
                    poetry run ci-artifacts --help
                    poetry run job-resource-usage --help
                    poetry run lockable-resources --help
                """);
            }

            stage("Create changelog") {
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
                        --additional_version_info="-rc${env.GERRIT_PATCHSET_NUMBER}.dev${env.GERRIT_CHANGE_NUMBER}" \
                        --print \
                        --output version.json
                """);
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

                            # adjust URL to this changelog in pyproject file, use double quotes for env variable usage
                            sed -i "s#CHANGE_ME_I_AM_A_CHANGELOG#release/\${CHANGELOG_VERSION}#" pyproject.toml

                            # create and publish tag
                            git tag -a v\$CHANGELOG_VERSION-rc${env.GERRIT_PATCHSET_NUMBER}.dev${env.GERRIT_CHANGE_NUMBER} -m "v\$CHANGELOG_VERSION-rc${env.GERRIT_PATCHSET_NUMBER}.dev${env.GERRIT_CHANGE_NUMBER}"
                            git tag --list
                            git push origin tag v\$CHANGELOG_VERSION-rc${env.GERRIT_PATCHSET_NUMBER}.dev${env.GERRIT_CHANGE_NUMBER}
                        """);
                    }
                }
            }

            stage("Build package") {
                sh(label: "build package", script: """
                    # see comment in pyproject.toml
                    poetry self add "poetry-dynamic-versioning[plugin]==1.4.1"
                    rm -rf dist/*
                    poetry build
                    poetry run twine check dist/*
                    python3 -m pip uninstall -y checkmk_dev_tools
                    python3 -m pip install --pre --user dist/checkmk_dev_tools-*-py3-none-any.whl
                """);
            }

            smart_stage(name: "Publish package", condition: release_new_version_flag, raiseOnError: false) {
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
