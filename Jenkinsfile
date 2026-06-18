#!/usr/bin/env groovy

REPO_NAME = 'django-elasticache'
SYSTEM_NAME = 'django-elasticache'
VENV = 'virtualenv'

@Library('monetate-jenkins-pipeline')
import org.monetate.Slack
def slack = new Slack(steps, REPO_NAME)

// Tag the current commit as v<version> and push only that tag to GitHub. Skips when the tag
// already exists at HEAD (rebuild), fails when it exists elsewhere. Credentials stay in shell
// env vars, never in git config, the command line, or the build log.
def pushReleaseTag(String version) {
    withCredentials([usernamePassword(credentialsId: 'git_monetate-jenkins', passwordVariable: 'GIT_PASSWORD', usernameVariable: 'GIT_USERNAME')]) {
        withEnv(["RELEASE_VERSION=${version}", "REPO_URL=https://github.com/monetate/${REPO_NAME}.git"]) {
            sh '''
                set -e
                set +x

                # https://github.blog/2022-04-18-highlights-from-git-2-36/#stricter-repository-ownership-checks
                # global --add because command-scope safe.directory needs git 2.38, guarded to avoid duplicates
                git config --global --get-all safe.directory | grep -qxF "$WORKSPACE" || \
                    git config --global --add safe.directory "$WORKSPACE"

                # ephemeral credential helper keeps the password out of process args and .git/config
                askgit() {
                    git -c credential.helper= -c credential.helper='!f() { printf "username=%s\npassword=%s\n" "$GIT_USERNAME" "$GIT_PASSWORD"; }; f' "$@"
                }

                # resolve the remote tag to its commit, an annotated tag ref holds a tag object whose SHA
                # never equals HEAD, so prefer the peeled ^{} line and fall back to plain for lightweight tags
                remote_tag_commit() {
                    remote_tags=$(askgit ls-remote --tags "$REPO_URL" "refs/tags/v$RELEASE_VERSION" "refs/tags/v$RELEASE_VERSION^{}")
                    peeled=$(printf '%s\n' "$remote_tags" | awk -v r="refs/tags/v$RELEASE_VERSION^{}" '$2 == r {print $1}')
                    plain=$(printf '%s\n' "$remote_tags" | awk -v r="refs/tags/v$RELEASE_VERSION" '$2 == r {print $1}')
                    if [ -n "$peeled" ]; then echo "$peeled"; else echo "$plain"; fi
                }

                remote_commit=$(remote_tag_commit)
                if [ -n "$remote_commit" ]; then
                    if [ "$remote_commit" = "$(git rev-parse HEAD)" ]; then
                        echo "Tag v$RELEASE_VERSION already exists at HEAD, skipping tag push"
                        exit 0
                    fi
                    echo "Tag v$RELEASE_VERSION already exists at $remote_commit which is not HEAD, refusing to release"
                    exit 1
                fi

                # a reused workspace can hold a tag from a build whose push failed
                if git rev-parse -q --verify "refs/tags/v$RELEASE_VERSION" > /dev/null; then
                    local_sha=$(git rev-parse "refs/tags/v$RELEASE_VERSION^{commit}")
                    if [ "$local_sha" != "$(git rev-parse HEAD)" ]; then
                        echo "Local tag v$RELEASE_VERSION points at $local_sha which is not HEAD, refusing to release"
                        exit 1
                    fi
                    echo "Reusing existing local tag v$RELEASE_VERSION at HEAD"
                else
                    git -c user.name=monetate-jenkins -c user.email=devops@monetate.com \
                        tag -a "v$RELEASE_VERSION" -m "Release v$RELEASE_VERSION"
                fi

                # a concurrent build can win the push race after the check above
                if ! askgit push "$REPO_URL" "refs/tags/v$RELEASE_VERSION"; then
                    if [ "$(remote_tag_commit)" = "$(git rev-parse HEAD)" ]; then
                        echo "Tag v$RELEASE_VERSION was pushed concurrently and points at HEAD, treating as success"
                        exit 0
                    fi
                    echo "Failed to push tag v$RELEASE_VERSION"
                    exit 1
                fi
            '''
        }
    }
}


pipeline {
    agent { label "node-v8" }
    environment {
        TMPDIR='/var/lib/jenkins/tmp'
        PYTHONUNBUFFERED=1
        FROM_JENKINS=true
    }
    options {
        buildDiscarder(logRotator(daysToKeepStr: '60'))
        timestamps()
    }
    stages {
        stage("Check if we should build") {
            steps {
                script {
                    RECENT_HISTORY = sh(script: "git log --since='4 days ago' --format=oneline origin/${GIT_BRANCH}", returnStdout: true).trim()
                    echo "${RECENT_HISTORY}"
                    if ("${RECENT_HISTORY}" == "") {
                        githubNotify context:'Check Age', description:'Skipping build because this branch is too old, please merge or rebase', status: 'FAILURE'
                        error "Skipping build because the recent log is too old, please rebase or add new commits"
                    }
                    githubNotify context:'Check Age', status: 'SUCCESS', description:'This PR is new enough'
                }
            }
        }

        stage("Slack start message") {
            steps {
                script { slack.success(this, ":pipeline: ${SYSTEM_NAME} pipeline started") }
            }
        }

        stage('Checkout source') {
            steps {
                checkout scm
            }
        }

        stage ('Clean') {
            when {
                anyOf {
                    branch 'master';
                    branch 'patch-*';
                    changelog '.*clean.*';
                }
            }
            steps {
                sh "make clean"
            }
        }

        stage('Test Monetate Package') {
            when {
                branch 'master'
            }
            steps {
                    script {
                        sh """
                            export PATH=/opt/pyenv/shims:/opt/pyenv/bin:/opt/monetate/virtualenv/monetate/bin:$PATH
                            export PYENV_ROOT=/opt/pyenv
                            export PYENV_VERSION=3.7:system
                            make test-tox
                        """
                    }
            }
            post {
                success {
                    script { slack.success(this, ":pipeline: ${SYSTEM_NAME} Package tests passed.") }
                }
                failure {
                     script { slack.failure(this, ":pipeline: ${SYSTEM_NAME} Package tests failed.") }
                }
            }
        }

         stage('Check/Release Monetate Package') {
            when {
                branch 'master'
            }
            steps {
                script {
                    // Check to see if VERSION file has changed, If so, release and tag.
                    def VERSION_BUMP = sh(script: "git diff --quiet HEAD\\^ HEAD -- ${env.WORKSPACE}/VERSION", returnStatus: true)
                    if ("${VERSION_BUMP}" == "1") {
                        def VERSION_NUMBER = readFile("${env.WORKSPACE}/VERSION").trim()
                        sh """
                            export PATH=/opt/pyenv/shims:/opt/pyenv/bin:/opt/monetate/virtualenv/monetate/bin:$PATH
                            export PYENV_ROOT=/opt/pyenv
                            export PYENV_VERSION=3.7:system
                            echo "Packaging/Releasing ${SYSTEM_NAME}-${VERSION_NUMBER}"
                            make release
                        """
                        pushReleaseTag(VERSION_NUMBER)
                        slack.success(this, ":pipeline: ${SYSTEM_NAME} Successfully published ${SYSTEM_NAME}-${VERSION_NUMBER} to artifactory.")
                    }
                }
            }
         }

        stage('Monetate Package PR Checks') {
            when {
                changeRequest()
            }
            steps {
                echo "Checking pull request..."
                script {
                    echo "Checking for changes..."
                    def res = sh(script: "git diff --quiet HEAD origin/master --", returnStatus: true)
                    if (res == 1) {
                        githubNotify context:"${SYSTEM_NAME} Package Tests", status: 'PENDING', description:'Running test suite...'
                        echo "Running tests for ${SYSTEM_NAME}"
                        sh """
                            env  # PATH=/usr/local/bin:/bin:/usr/bin
                            export PATH=/opt/pyenv/shims:/opt/pyenv/bin:/opt/monetate/virtualenv/monetate/bin:$PATH
                            export PYENV_ROOT=/opt/pyenv
                            export PYENV_VERSION=3.7:system
                            make test-tox
                        """
                    }
                }
            }
            post {
                success {
                    githubNotify context:"${SYSTEM_NAME} Package Tests", status: 'SUCCESS', description:'Test suite passed'
                }
                failure {
                    githubNotify context:"${SYSTEM_NAME} Package Tests", status: 'FAILURE', description:'Test suite failed'
                }
            }
        }
    }
    post {
        always {
            script { slack.currentResult(this, ":pipeline: ${SYSTEM_NAME} pipeline finished") }
        }
        fixed {
            script { slack.currentResult(this, ":pipeline: ${SYSTEM_NAME} pipeline fixed", "#product-engineering") }
        }
        failure {
            script { slack.currentResult(this, ":pipeline: ${SYSTEM_NAME} pipeline failed", "#product-engineering") }
        }
    }
}
