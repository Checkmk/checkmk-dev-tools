#!groovy

/// file: integration.groovy

def main() {
    def image_name = "python-curl-poetry";
    def dockerfile = "ci/Dockerfile";
    def docker_args = "${mount_reference_repo_dir}";
    def release_new_version_flag = false;

    currentBuild.description += (
        """
        |Reason:            ${env.GERRIT_EVENT_TYPE}<br>
        |Commit SHA:        ${env.GERRIT_PATCHSET_REVISION}<br>
        |Patchset Number:   ${env.GERRIT_PATCHSET_NUMBER}<br>
        |""".stripMargin());

    dir("${checkout_dir}") {
        def docker_image = docker.build(image_name, "-f ${dockerfile} .");
        docker_image.inside(docker_args) {
            stage("Install") {
                sh(label: "Install script", script: """
                    poetry --version

                    # install scripts of this repo
                    poetry install

                    # print current version
                    poetry run ci-artifacts --version

                    poetry run ci-artifacts --help
                    poetry run ci-artifacts validate --help
                """);
            }

            withCredentials([
                usernamePassword(
                    credentialsId: 'jenkins-api-token',
                    usernameVariable: 'JENKINS_USERNAME',
                    passwordVariable: 'JENKINS_PASSWORD'
                ),
                string(
                    credentialsId: 'INFLUXDB-Jenkins-read-official',
                    variable: 'INFLUXDB_READ_TOKEN',
                ),
            ]) {
                stage("Test subcommands") {
                    sh(label: "Test subcommand", script: """
                        poetry run pytest -vvvvv -s
                    """);
                }
            }
        }
    }
}

return this;
