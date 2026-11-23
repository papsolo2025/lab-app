// orders-api CI/CD pipeline
// test -> SonarQube quality gate -> Trivy (code) -> build -> Trivy (image) -> ECR -> Helm chart to Nexus
// -> deploy dev -> (main only) staging -> manual approval -> prod
//
// Needs these Jenkins credentials:  sonar-token (secret text), nexus-ci (username/password),
// slack-webhook (secret text, optional).  Global env vars NEXUS_URL, SONAR_URL, AWS_REGION and
// CLUSTER_NAME come from Jenkins Configuration as Code.

pipeline {
  agent any

  options {
    timestamps()
    timeout(time: 45, unit: 'MINUTES')
    buildDiscarder(logRotator(numToKeepStr: '20'))
    disableConcurrentBuilds()
  }

  environment {
    APP        = 'orders-api'
    CHART      = 'helm/orders-api'
    KUBECONFIG = "${WORKSPACE}/.kube/config"
  }

  stages {
    stage('Init') {
      steps {
        script {
          env.ACCOUNT_ID = sh(script: 'aws sts get-caller-identity --query Account --output text', returnStdout: true).trim()
          env.REGISTRY   = "${env.ACCOUNT_ID}.dkr.ecr.${env.AWS_REGION}.amazonaws.com"
          env.IMAGE_TAG  = "${env.GIT_COMMIT.take(7)}-${env.BUILD_NUMBER}"
          env.IMAGE      = "${env.REGISTRY}/${env.APP}:${env.IMAGE_TAG}"
          env.NEXUS_HOST = env.NEXUS_URL.replaceAll('https?://', '').replaceAll(':.*', '')
          currentBuild.displayName = "#${env.BUILD_NUMBER} ${env.IMAGE_TAG}"
        }
      }
    }

    stage('Unit tests') {
      steps {
        // Same path inside and outside the container so coverage.xml paths match for SonarQube.
        sh '''
          docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp \
            -v "$WORKSPACE:$WORKSPACE" -w "$WORKSPACE" python:3.13-slim sh -c "
              python -m venv /tmp/venv &&
              /tmp/venv/bin/pip install -q \
                --index-url $NEXUS_URL/repository/pypi-proxy/simple --trusted-host $NEXUS_HOST \
                -r requirements.txt -r requirements-dev.txt &&
              /tmp/venv/bin/pytest --cov=orders_api --cov-report=xml --junitxml=test-results.xml"
        '''
      }
      post {
        always { junit 'test-results.xml' }
      }
    }

    stage('SonarQube + quality gate') {
      steps {
        withCredentials([string(credentialsId: 'sonar-token', variable: 'SONAR_TOKEN')]) {
          sh '''
            docker run --rm -e SONAR_HOST_URL="$SONAR_URL" -e SONAR_TOKEN \
              -v "$WORKSPACE:$WORKSPACE" -w "$WORKSPACE" sonarsource/sonar-scanner-cli \
              -Dsonar.projectVersion="$IMAGE_TAG" \
              -Dsonar.qualitygate.wait=true -Dsonar.qualitygate.timeout=300
          '''
        }
      }
    }

    stage('Trivy: code, secrets, IaC') {
      steps {
        sh '''
          trivy fs --scanners vuln,secret --severity CRITICAL --ignore-unfixed --exit-code 1 .
          trivy config --severity HIGH,CRITICAL --exit-code 0 "$CHART"   # report only (for now)
        '''
      }
    }

    stage('Build image') {
      steps {
        sh 'docker build --pull -t "$IMAGE" --label git-commit="$GIT_COMMIT" .'
      }
    }

    stage('Trivy: image') {
      steps {
        sh '''
          trivy image --format json -o trivy-image.json "$IMAGE"
          trivy image --severity CRITICAL --ignore-unfixed --exit-code 1 "$IMAGE"
        '''
      }
    }

    stage('Push to ECR') {
      steps {
        sh '''
          aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$REGISTRY"
          docker push "$IMAGE"
        '''
      }
    }

    stage('Publish Helm chart to Nexus') {
      steps {
        withCredentials([usernamePassword(credentialsId: 'nexus-ci', usernameVariable: 'NEXUS_USER', passwordVariable: 'NEXUS_PASS')]) {
          sh '''
            helm lint "$CHART"
            helm package "$CHART" --version "0.1.$BUILD_NUMBER" --app-version "$IMAGE_TAG" -d dist
            curl -fsS -u "$NEXUS_USER:$NEXUS_PASS" --upload-file dist/*.tgz "$NEXUS_URL/repository/helm-hosted/"
          '''
        }
      }
    }

    stage('Deploy: dev') {
      steps {
        sh '''
          mkdir -p "$(dirname "$KUBECONFIG")"
          aws eks update-kubeconfig --name "$CLUSTER_NAME" --region "$AWS_REGION"
        '''
        deploy('dev')
      }
    }

    stage('Deploy: staging') {
      when { branch 'main' }
      steps { deploy('staging') }
    }

    stage('Approve prod') {
      when { branch 'main' }
      steps {
        timeout(time: 1, unit: 'DAYS') {
          input message: "Deploy ${env.IMAGE_TAG} to PROD?", ok: 'Deploy'
        }
      }
    }

    stage('Deploy: prod') {
      when { branch 'main' }
      steps { deploy('prod') }
    }
  }

  post {
    always {
      archiveArtifacts artifacts: 'trivy-*.json, dist/*.tgz', allowEmptyArchive: true
      sh 'docker image rm "$IMAGE" || true'
    }
    success { notify(":white_check_mark: ${env.JOB_NAME} #${env.BUILD_NUMBER} ${env.IMAGE_TAG} succeeded") }
    failure { notify(":x: ${env.JOB_NAME} #${env.BUILD_NUMBER} FAILED - ${env.BUILD_URL}") }
    cleanup { cleanWs() }
  }
}

// Helm upgrade + rollout wait + smoke test. Helm rolls back automatically if the rollout fails.
// No --create-namespace: namespaces are created by the platform (install.sh) and Jenkins only has
// edit rights inside dev/staging/prod - it can't create namespaces (least privilege).
def deploy(String ns) {
  sh """
    helm upgrade --install ${env.APP} "${env.CHART}" --namespace ${ns} \
      -f "${env.CHART}/values-${ns}.yaml" \
      --set image.repository="${env.REGISTRY}/${env.APP}" --set image.tag="${env.IMAGE_TAG}" \
      --set export.bucket="devops-lab-orders-exports-${env.ACCOUNT_ID}" \
      --rollback-on-failure --wait --timeout 5m
    kubectl -n ${ns} exec deploy/${env.APP} -- python -c \
      "import urllib.request; print(urllib.request.urlopen('http://${env.APP}.${ns}.svc.cluster.local/health/ready', timeout=10).read().decode())"
  """
}

def notify(String text) {
  try {
    withCredentials([string(credentialsId: 'slack-webhook', variable: 'SLACK_URL')]) {
      writeJSON file: 'slack.json', json: [text: text]
      sh 'curl -fsS -X POST -H "Content-Type: application/json" --data @slack.json "$SLACK_URL" >/dev/null'
    }
  } catch (err) {
    echo "Slack notification skipped: ${err.message}"
  }
}
