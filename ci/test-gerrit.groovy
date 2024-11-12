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
        |Reason:       ${env.GERRIT_EVENT_TYPE}<br>
        |Commit SHA:   ${env.GERRIT_PATCHSET_REVISION}<br>
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
    }
}

return this;
