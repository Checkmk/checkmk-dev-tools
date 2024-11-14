#!groovy

/// file: release.groovy

def main() {
    def image_name = "python-curl-poetry";
    def dockerfile = "ci/Dockerfile";
    def docker_args = "${mount_reference_repo_dir}";
    def parsed_version = "";
    def publish_package = false;

    currentBuild.description += (
        """
        |May create an automatic release<br>
        |""".stripMargin());

    dir("${checkout_dir}") {
        cmd_output("git describe --exact-match --tags") ?: error("Automatic releases only from a tag");

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
                        --in-place
                """);
                parsed_version = sh(label: "parse changelog", returnStdout: true, script: """
                    dev/run-in-venv \
                        changelog2version \
                        --changelog_file changelog.md \
                        --version_file cmk_dev/version.py \
                        --version_file_type py \
                        --print | jq -r .info.version
                """);
                println("Parsed version from changelog: ${parsed_version}");
            }
            archiveArtifacts(allowEmptyArchive: true, fingerprint: true, artifacts: 'changelog.md');
        }

        stage("Build and publish package") {
            docker_image.inside(docker_args) {
                sh(label: "build package", script: """
                    rm -rf dist/*
                    poetry build
                    poetry run twine check dist/*
                    python3 -m pip uninstall -y checkmk_dev_tools
                    python3 -m pip install --pre --user dist/checkmk_dev_tools-*-py3-none-any.whl
                """);
                sh(label: "publish package", script: """
                    poetry publish --skip-existing
                """);
            }
            archiveArtifacts(allowEmptyArchive: true, fingerprint: true, artifacts: 'dist/checkmk_dev_tools-*');
        }

        /*
        conditional_stage("Publish package", publish_package) {
            withCredentials(
                bindings: [
                    usernamePassword(
                        credentialsId: 'jenkins-api-token', // TODO use real creds for PyPI
                        usernameVariable: 'JJB_USER',
                        passwordVariable: 'JJB_PASSWORD'
                    )
                ]
            ) {
                docker_image.inside(docker_args) {
                    sh(label: "build package", script: """
                    """);
                }
            }
        }
        */
    }
}

return this;
