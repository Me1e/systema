## RAGFlow 클라우드 리눅스 서버 배포 가이드

이 문서는 로컬 macOS 개발 환경에서 수정한 내역을 원상 복구하고, 프로덕션 환경에 권장되는 방식으로 RAGFlow를 클라우드 리눅스 서버에 배포하는 절차를 안내합니다.

### 1. 목표

- 보안이 강화된 프로덕션 환경 구성
- gVisor를 활용한 안전한 코드 실행(샌드박스) 환경 활성화
- 로컬 개발을 위해 수정했던 모든 코드와 설정을 원본 상태로 복구

### 2. 서버 사전 준비 사항

클라우드에서 생성한 리눅스 서버(예: Ubuntu 22.04 LTS)에 다음 항목들이 설치되어 있어야 합니다.

1.  **Docker & Docker Compose:** RAGFlow의 모든 서비스는 컨테이너로 실행됩니다.

    - [Docker 공식 설치 가이드](https://docs.docker.com/engine/install/ubuntu/)
    - [Docker Compose 공식 설치 가이드](https://docs.docker.com/compose/install/)

2.  **gVisor:** **(필수)** 코드 실행 에이전트 기능을 안전하게 사용하기 위한 필수 보안 샌드박스입니다. 로컬에서는 비활성화했지만, 서버 환경에서는 반드시 설치하고 설정해야 합니다.

    - [gVisor 공식 설치 가이드](https://gvisor.dev/docs/user_guide/install/)

3.  **시스템 요구사항 확인:** `vm.max_map_count` 값이 `262144` 이상인지 확인하고, 아니라면 설정해야 합니다. (자세한 방법은 프로젝트 `README.md` 참고)

### 3. 배포 절차

#### Step 1: 로컬 개발용 변경사항 원상 복구

로컬에서 개발 편의를 위해 수정했던 파일들을 `git`을 사용하여 원본 상태로 완벽하게 되돌립니다. 아래 명령어들을 프로젝트의 루트 디렉토리에서 실행하세요.

I will now run a series of `git restore` commands to revert the changes made to the sandbox, docker-compose, and Dockerfile configurations, bringing the project back to its original state before our local development modifications.

```bash
git restore sandbox/executor_manager/core/container.py && git restore docker/docker-compose-base.yml && git restore sandbox/executor_manager/Dockerfile
```

네, 방금 명령어를 실행하여 로컬에서 수정했던 모든 파일을 원본 상태로 되돌렸습니다. 이제 프로젝트는 처음 클론했을 때와 동일한 깨끗한 상태가 되었습니다.

#### Step 2: 프로덕션용 환경 설정

서버 환경에서는 개발 시 사용했던 `docker-compose-base.yml`이 아닌, 전체 서비스가 포함된 **`docker-compose.yml`** 파일을 사용합니다.

1.  **`.env` 파일 확인 및 수정:**
    프로젝트의 `docker/.env` 파일을 열어 아래 항목들의 비밀번호를 **반드시 강력하고 안전한 값으로 변경**해 주세요.

    - `ELASTIC_PASSWORD`
    - `MYSQL_PASSWORD`
    - `MINIO_PASSWORD`
    - `REDIS_PASSWORD`

2.  **샌드박스 활성화 (선택 사항):**
    `docker/.env` 파일 맨 아래를 보면 `sandbox` 프로파일 설정이 있습니다. 코드 실행 기능을 사용하려면 이 부분의 주석을 제거하여 활성화하세요. (gVisor가 설치된 경우에만 가능)

    ```
    # In docker-compose.yml, services with the profile "sandbox" are disabled by default.
    # To enable them, uncomment the following line.
    COMPOSE_PROFILES=sandbox
    ```

#### Step 3: RAGFlow 서비스 시작

이제 모든 준비가 완료되었습니다. 아래 명령어로 RAGFlow 전체 서비스를 시작합니다.

```bash
# ragflow/docker 디렉토리로 이동
cd docker

# 프로덕션용 docker-compose.yml 파일로 서비스 시작
docker compose -f docker-compose.yml up -d
```

- **GPU 서버인 경우:** `docker-compose-gpu.yml` 파일을 사용하세요.
  ```bash
  docker compose -f docker-compose-gpu.yml up -d
  ```

`up -d` 명령어는 백그라운드에서 모든 서비스를 실행합니다. `docker ps` 명령어로 모든 컨테이너가 정상적으로 실행 중인지 확인할 수 있습니다.

### 4. 배포 후 확인 사항

1.  웹 브라우저에서 `http://<서버_IP_주소>` 로 접속하여 RAGFlow UI가 정상적으로 보이는지 확인합니다. (방화벽에서 80번 포트가 열려있어야 합니다.)
2.  로그인 후, **Model providers** 페이지에서 사용할 LLM의 API 키를 설정합니다.
3.  지식 베이스를 생성하고 문서를 업로드하여 파일 처리(Parsing)가 정상적으로 동작하는지 테스트합니다.
4.  샌드박스를 활성화했다면, 코드 실행 기능이 포함된 에이전트를 생성하여 gVisor 위에서 안전하게 코드가 실행되는지 테스트합니다.

이제 클라우드 리눅스 서버에 보다 안전하고 안정적인 방식으로 RAGFlow가 배포되었습니다.
