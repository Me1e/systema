# RAGFlow 로컬 개발 환경 개인화 기록 (Personalization Log)

이 문서는 RAGFlow 프로젝트의 기본 설정에서 벗어나 특정 로컬 개발 환경에 맞게 수정된 내역을 기록합니다.

## 1. 개발 환경 요약

- **운영체제 (OS):** macOS (Apple Silicon, ARM64)
- **핵심 목표:**
  1.  Apple Silicon MacBook에서 개발 모드로 RAGFlow 실행
  2.  로컬 임베딩 모델 없이 외부 API를 통해 임베딩 수행 (Slim 버전)
  3.  **gVisor 없이** 코드 실행기(Sandbox) 기능 사용

## 2. 원본 소스에서 변경된 사항

gVisor 없이 코드 실행기(Sandbox)를 로컬에서 구동하기 위해 다음 세 가지 주요 파일을 수정했습니다.

### 변경 사항 1: gVisor 런타임 의존성 제거

- **수정 파일:** `sandbox/executor_manager/core/container.py`
- **변경 내용:** Docker 컨테이너 생성 시 gVisor 런타임(`runsc`)을 강제하는 코드를 주석 처리했습니다.
  ```python
  # ...
  container: DockerContainer = await self.client.containers.create(
      image,
      command,
      # runtime="runsc",  <-- 이 부분을 주석 처리함
      **self.container_config,
  )
  # ...
  ```
- **변경 이유:** 사용자의 macOS 환경에는 gVisor가 설치되어 있지 않으므로, Docker의 기본 런타임(`runc`)으로 컨테이너를 실행하도록 변경했습니다. **이것은 보안 격리 수준을 낮추므로 로컬 개발 및 테스트 용도로만 사용해야 합니다.**

### 변경 사항 2: Sandbox 서비스 로컬 빌드 강제

- **수정 파일:** `docker/docker-compose-base.yml`
- **변경 내용:** `sandbox-executor-manager` 서비스가 원격의 미리 빌드된 이미지를 사용하는 대신, 로컬 소스 코드를 기반으로 직접 빌드되도록 설정을 변경했습니다.

  ```yaml
  # 기존 설정 (image: ... 를 사용)
  # image: ${SANDBOX_EXECUTOR_MANAGER_IMAGE-infiniflow/sandbox-executor-manager:latest}

  # 변경된 설정 (build: ... 를 사용)
  sandbox-executor-manager:
    build:
      context: ../
      dockerfile: ./sandbox/executor_manager/Dockerfile
    image: sandbox-executor-manager:latest
    # ... (포트, 볼륨 등 기타 설정도 함께 수정됨)
  ```

- **변경 이유:** 위 **변경 사항 1**에서 수정한 로컬 소스 코드(`container.py`)가 실제 실행되는 컨테이너에 반영되게 하기 위함입니다.

### 변경 사항 3: Dockerfile 내 의존성 경로 수정

- **수정 파일:** `sandbox/executor_manager/Dockerfile`
- **변경 내용:** 의존성 패키지 설치 명령어의 `requirements.txt` 파일 경로를 빌드 컨텍스트에 맞게 수정했습니다.
  ```dockerfile
  # 기존: RUN uv pip install --system -r requirements.txt (오류 발생)
  # 최종 수정: RUN uv pip install --system -r /app/sandbox/executor_manager/requirements.txt
  ```
- **변경 이유:** `docker-compose-base.yml`에서 지정한 빌드 컨텍스트 (`context: ../`) 때문에 Dockerfile 내에서 파일을 찾는 기준 경로가 변경되어, 올바른 절대 경로로 수정해야 했습니다.

## 3. 로컬 환경 실행 절차 (요약)

위 변경사항이 적용된 상태에서 이 프로젝트를 다시 실행할 경우, 아래 절차를 따르면 됩니다.

1.  **의존 서비스 실행 (Docker):**
    ```bash
    # --build 플래그를 사용하여 sandbox-executor-manager 이미지를 소스코드 기반으로 재빌드
    docker compose -f docker/docker-compose-base.yml up -d --build
    ```
2.  **호스트 파일 확인 (`/etc/hosts`):** 아래 내용이 등록되어 있는지 확인합니다.
    ```
    127.0.0.1       es01 infinity mysql minio redis sandbox-executor-manager
    ```
3.  **백엔드 서비스 실행:** (별도 터미널에서)
    ```bash
    source .venv/bin/activate
    export PYTHONPATH=$(pwd)
    bash docker/launch_backend_service.sh
    ```
4.  **프론트엔드 서비스 실행:** (또 다른 별도 터미널에서)
    ```bash
    cd web
    npm run dev
    ```
5.  **LLM/임베딩 모델 API 키 설정:** 웹 UI 접속 후 "Model providers" 페이지에서 사용할 모델의 API 키를 등록합니다.
