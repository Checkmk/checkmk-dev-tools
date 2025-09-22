#!groovy

/// file: integration.groovy

def main() {
    def image_name = "python-curl-uv";
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
                    uv --version

                    # install scripts of this repo
                    uv sync

                    # print current version
                    dev/run-in-venv ci-artifacts --version

                    dev/run-in-venv ci-artifacts --help
                    dev/run-in-venv ci-artifacts validate --help
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
                        dev/run-in-venv pytest -vvvvv -s
                    """);
                }
            }
        }
    }
}

return this;
