#!groovy

/// file: test-job.groovy

def main() {
    dir("${checkout_dir}") {
        stage("Create test file") {
            sh("""
                echo "${CUSTOM_GIT_REF}" > test-file.txt
            """);
        }

        stage("Archive stuff") {
            show_duration("archiveArtifacts") {
                archiveArtifacts(
                    allowEmptyArchive: true,
                    artifacts: "test-file.txt",
                    fingerprint: true,
                );
            }
        }
    }
}

return this;
