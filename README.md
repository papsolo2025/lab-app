# lab-app - orders-api
Small Python service used to practise CI/CD, Kubernetes, monitoring and SRE.

- `orders_api/` - Flask app with Prometheus metrics and chaos switches
- `tests/` - pytest suite (`pytest --cov=orders_api`)
- `Dockerfile` - multi-stage, non-root
- `helm/orders-api/` - Deployment, Service, HPA, PDB, ServiceMonitor, ALB Ingress; values per environment
- `Jenkinsfile` - test, SonarQube, Trivy, build, ECR, Helm chart to Nexus, deploy dev/staging/prod
