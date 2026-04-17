## Introduction

This repository covers the kubernetes charts and configuration for the deployment of CELINE project.

- `./.sops` and `.sops.yaml` carry the secret keys for SOPS secret management
- `./charts/**` contains local chart to deploy resources. See next chapter for the list.
- `./envs/**` contains per environment `values.yaml`, `secrets.yaml` (SOPS managed) and chart values specific overrides.
- `./helmfile.d` contains the helmfile definitions, groups and references to the charts
- `./defaults` provides the point of integration between the `envs/**/*.yaml` and the `charts/**/values.yaml`. This to have simpler envs variables and reuse of parameters across different charts.

`taskfile.yaml` contains wrappers scripts to operate in a selected environment.

Development setup is managed with `minikube` with local build support via `skaffold`

## Local Charts

- `celine-services` Base chart used to normalize settings between `celine-*` charts
- `api-gateway` wraps all CELINE APIs via a unique ingress eg. api.domain.tld/my-service

- `celine-dataset-api` Dataset API
- `celine-dataset-api-shell` Dataset API CLI to manage datasets
- `celine-digital-twin` Digital Twin API 
- `celine-flexibility-api` Flexibility API  
- `celine-mqtt-auth` mosquitto-go-auth compatible API endpoint for MQTT auth/ACL 
- `celine-nudging` Nudging API  
- `celine-policies-shell` Policies CLI to manage keycloak
- `celine-rec-registry` REC Registry API  
- `celine-rec-registry-shell` REC Registry CLI to manage RECs organizations and assets metadata

- `celine-ai-assistant` AI Assistant API
- `celine-roi` ROI API
- `celine-webapp` Participant webapp API
- `celine-grid` Grid resilience API
- `celine-frontend-assistant` AI Assistant webapp 
- `celine-frontend-roi` ROI webapp 
- `celine-frontend-webapp` Participant webapp 
- `celine-frontend-grid` Grid webapp 

- `auth-setup` configure secrets and configmaps for `oauth2-proxy` and `keycloak`
- `mqtt-setup` Configure MQTT access for services
- `mqtt-ingestor` Ingest to a database all MQTT messages incoming on configured topics
- `marquez` Marquez OpenLineage endpoints and UI
- `mosquitto-go-auth` mosquitto with mosquitto-go-auth module
- `pg-freezer` Cold storage service that collects records from tables and mirror to minio/s3 as parquet, cleaning up tables
- `postgres-db` CNPG specific configurations for database/users maps
- `prefect-pipelines` Collects and deploy the data pipelines of CELINE
- `registry-accounts` Configure docker/ghcr.io secrets for image pulling
- `s3-accounts` Configure minio/s3 access credentials
- `tls-setup` Configure TLS adapted for local (self signed certs) vs production (let's encrypt) environments
