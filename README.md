# Codex Toolkit

macOS와 Linux에서 Codex CLI·VS Code 확장과 개발용 MCP를 한 번에 설치하고,
`~/.codex`의 공통 설정을 관리하는 개인용 설치 번들입니다.

## 설치되는 구성

| 구분 | 구성 요소 |
|---|---|
| Codex | 공식 native Codex CLI, VS Code `openai.chatgpt` 확장 |
| MCP | Serena, Headroom, Mem0 (Graft는 설치·등록 대상에서 제외) |
| 지침·플러그인 | Ponytail 지침, Superpowers 플러그인 |
| 편의 명령 | `codex-update`, `codex-doctor`, `mem0-import` |

Mem0는 Oracle AI Vector Search와 사용자가 지정한 추론·임베딩 API를 사용합니다.
Headroom은 MCP 도구로만 동작하며 Codex 모델 요청을 프록시하지 않습니다.

## 설치 전 확인

> 기존 `~/.codex`의 로그인과 대화 기록을 유지하며 Codex와 툴킷을 설치·갱신합니다.
> 변경할 전역 설정은 `~/.codex-backups/update-*`에 백업합니다.
> 프로젝트 안의 `.codex/config.toml`과 `AGENTS.md`는 건드리지 않습니다.

- 지원 환경: macOS 또는 Linux, Bash 3.2 이상
- 사전 명령: Homebrew, curl, git, Bun
- Python은 기존 toolkit 가상환경 → 실행 전 PATH의 `python3` → PATH의 Python 3.13/3.12/3.11 순서로 재사용합니다.
  지원 범위는 Python 3.11–3.13이며 SSL, sqlite3, ctypes, tomllib, venv 모듈을 확인합니다.
  호환되는 Python이 없을 때만 Homebrew `python@3.13`과 그 의존성(`openssl@3` 등)을 설치합니다.
  기존 가상환경이 손상되거나 지원 범위를 벗어나면 보존한 채 중단합니다.
- 시작 점검에서 `uv`가 없으면 Homebrew로 자동 설치합니다.
- 패키지 빌드에 필요한 make 또는 C/C++ 컴파일러가 없으면 Linux 패키지 관리자로
  자동 설치합니다. Ubuntu/Debian/WSL에서는 `build-essential`을 설치하며,
  dnf, yum, pacman, zypper, apk도 지원합니다. `uv`와 빌드 도구가 모두 없으면 둘 다 설치합니다.
  macOS에서는 Xcode Command Line Tools를 미리 설치하세요(`xcode-select --install`).
- 설치 디렉터리는 `~/.codex` 밖에 두고 일반 사용자로 실행합니다. 스크립트 전체를
  `sudo`로 실행하지 마세요. Linux 빌드 도구 자동 설치 단계에서만 `sudo`를 사용하며
  비밀번호를 요구할 수 있습니다. 설치 후 명령을 다시 점검하며 실패하면 중단합니다.
- 실행 중인 Codex CLI, Codex 앱, VS Code를 모두 종료하세요.

## 셸 스크립트 사용법

| 명령 | 용도 |
|---|---|
| `bash setup.sh` | 기존 로그인·대화 기록을 유지하며 설치 또는 갱신 |
| `bash setup.sh --resume` | 기존 `~/.codex`를 유지하면서 실패·누락된 설치와 설정을 다시 맞춤 |
| `bash update.sh` | 설치된 툴킷과 패키지를 업데이트하고 설정을 재적용 |
| `bash remove.sh --dry-run` | 삭제 대상과 실행할 제거 명령만 확인 |
| `bash remove.sh` | toolkit 구성요소·Codex native CLI와 설정 연동 제거 |
| `bash setup.sh --help` | 지원 옵션과 환경 변수 확인 |

`setup.sh`와 `update.sh`는 다음 작업을 순서대로 수행합니다.

1. 운영체제, 필수 명령과 실행 중인 Codex 프로세스를 검사합니다.
2. 공식 native installer로 Codex를 설치하거나 업데이트하고(기존 Homebrew Codex formula/cask는 제거), MCP 의존성과 등록된 Serena 프로젝트의 언어 서버를 다시 맞춥니다.
3. `~/.codex/config.toml`, 전역 `AGENTS.md`, shell alias를 구성합니다.
4. Mem0 연결 정보와 hook, Headroom의 token/balanced MCP 모드를 구성합니다. Superpowers 설치 전에 로그인을 확인하며, 대화형 터미널에서 필요하면 ChatGPT 로그인을 진행합니다.
5. 설정 유효성과 모든 활성 MCP의 실제 시작·도구 목록 및 최종 상태를 검사합니다.

Codex native CLI는 기본적으로 `~/.local/bin/codex`에 설치됩니다.

### 최초 설치

```bash
bash setup.sh
```

설치 중 Mem0 연결에 필요한 Oracle wallet 디렉터리, DB 계정, TNS alias와 wallet
비밀번호, 추론·임베딩 API URL을 입력합니다. 각 URL 프롬프트 전에
`http://HOST:1234/v1/chat/completions`, `http://HOST:1234/v1/embeddings` 예시가 표시됩니다.
입력 URL은 권한 `600`인 `~/.codex/mem0.json`에만 저장됩니다.

완료 후 새 터미널을 열고 다음 순서로 확인합니다.

```bash
codex login status
codex-doctor
codex
```

Codex의 `/mcp`에서 연결 상태를 확인하세요. 자동 메모리를 사용하려면 `/hooks`에서
Mem0의 `SessionStart`·`Stop`·`SessionEnd` hook을 검토하고 신뢰해야 합니다.

정상 완료된 `setup.sh`는 Mem0 구성과 Headroom 읽기 캐시 검증까지 마친 상태이므로
`--resume`을 다시 실행할 필요가 없습니다.

비대화형 실행에서 로그인이 없거나 Superpowers 설치가 실패하면 나머지 MCP 검사는 계속되지만,
최종 결과는 `Partial setup`과 종료 코드 1로 표시합니다. 인증 또는 워크스페이스 관리자 허용
문제를 해결한 뒤 다음 명령으로 다시 설치할 수 있습니다. 브라우저를 열 수 없는 원격 환경에서는
`codex login --device-auth`를 사용할 수 있습니다.

```bash
codex login
codex plugin add superpowers@openai-curated-remote
```

### `--resume`의 역할

`--resume`은 실패한 한 줄에서 그대로 이어가는 옵션이 아니라, 기본 설치와 같이
기존 데이터를 유지하며 설치 전체를 다시 조정하는 복구 모드입니다.

- `~/.codex`를 이동하거나 새로 만들지 않아 로그인, 대화 기록과 사용자 설정을 유지합니다.
- 설치 상태를 재사용하면서 패키지, MCP 실행 경로, 전역 지침과 hook을 다시 확인합니다.
- Mem0 설정 파일이 없으면 Oracle 연결 정보와 모델 API URL을 다시 입력받고, 있으면 기존 값을 재사용합니다.
- 이전 버전의 Graft MCP 등록·실행 파일과 toolkit 전용 npm 패키지를 정리합니다.
- 모든 설치 단계를 다시 실행하므로 네트워크가 필요하고 다소 시간이 걸릴 수 있습니다.

다음 경우에 실행하세요.

- 최초 설치가 오류나 강제 종료로 중단된 경우
- `Partial setup`이 표시된 경우
- Mem0 입력을 완료하지 못했거나 hook/MCP 등록을 다시 적용해야 하는 경우
- uv 등 실행 환경의 경로가 바뀐 경우
- Headroom 설정을 복구한 경우

```bash
bash setup.sh --resume
```

원본 설치 디렉터리가 없다면 설치 중 복사된 스크립트를 사용합니다.

```bash
bash ~/.codex/toolkit/bundle/setup.sh --resume
```

설치 상태가 아직 없는 경우에도 `--resume`으로 다시 시작할 수 있습니다.

### 설치 후 Mem0 설정 완료

최초 설치가 Mem0 단계 전에 멈췄거나 `~/.codex/mem0.json`이 아직 없다면
`--resume`을 실행해 Oracle 설정 입력과 MCP·hook 등록을 완료해야 합니다.

```bash
bash ~/.codex/toolkit/bundle/setup.sh --resume
codex-doctor
```

기존 `mem0.json`의 값은 `--resume`에서 덮어쓰거나 다시 묻지 않습니다. 다만 이전
버전에서 생성된 파일처럼 모델 API URL이 없는 경우에는 누락된 URL만 입력받습니다.
연결 정보 자체를 바꿀 때는 권한 `600`을 유지하며 해당 파일을 먼저 수정한 뒤
`--resume`을 실행하세요. 완료 후 Codex를 다시 시작해 `/mcp`에서 `mem0`,
`/hooks`에서 세 hook을 확인합니다. 기존 설치에 `Stop`이 없다면 업데이트된 설치 번들에서 아래 명령으로
요청 완료 시 자동 저장을 추가한 뒤 Codex를 다시 시작하고 새 hook을 검토·신뢰하세요.

```bash
~/.codex/toolkit/venv/bin/python ~/.codex/toolkit/bundle/configure.py mem0-hooks
```

### 구성요소 제거

Codex·VS Code를 종료한 후 `bash remove.sh --dry-run`으로 대상을 확인하고
`bash remove.sh`를 실행합니다. 원본 저장소가 없으면
`bash ~/.codex/toolkit/bundle/remove.sh`도 사용할 수 있습니다.

toolkit 전용 Serena·Headroom·Mem0 런타임, Ponytail 체크아웃, 이전 npm 도구,
native Codex CLI, toolkit MCP·hook·shell 연동과 Mem0 재시도 서비스를 제거합니다.
등록된 Superpowers는 Codex의 제거 명령을 사용하며, 이 버전 이후 toolkit이 새로 설치한
VS Code 확장도 제거합니다. 이전 버전은 확장 소유권을 기록하지 않아 기존 확장을 보존합니다.
수정 전 설정, Mem0 연결 정보와 미처리 작업은 권한이 제한된
`~/.codex-backups/remove-*`에 백업합니다. 로그인·대화 기록, 다른 플러그인과
사용자 설정, 공유 Homebrew/Python/uv/컴파일러·모델 캐시, Oracle의 벡터 데이터는
보존합니다. 설치 전의 `reset-*` 백업도 자동 복원하거나 삭제하지 않습니다.

설치 이력이 없거나 심볼릭 링크 때문에 삭제 범위가 불명확하면 제거를 중단합니다.
이미 toolkit이 제거된 상태에서 다시 실행해도 사용자 파일은 삭제하지 않습니다.

## 운영 명령

| 작업 | 명령 |
|---|---|
| 전체 도구 업데이트 | `codex-update` |
| 업데이트 스크립트 직접 실행 | `bash ~/.codex/toolkit/bundle/update.sh` |
| MCP 연결 검사 | `codex-doctor` |
| Mem0 대기 작업 재처리 | `~/.codex/toolkit/bin/mem0-session --drain` |

업데이트는 사용자 설정과 Mem0 연결 정보를 보존하며, 실행 전에 설정을
`~/.codex-backups/update-*`에 백업합니다. 일반적인 버전 갱신은 `update.sh`,
설치 실패나 구성 복구는 `setup.sh --resume`을 사용하세요.

## Mem0

설정에는 chat completion과 embedding API URL만 입력합니다. 설정 검사와 실제
호출은 LM Studio의 모델 목록에서 종류별로 현재 로드된 모델을 선택하며,
모델이 여러 개면 서버 목록의 첫 번째 로드된 인스턴스를 사용합니다.
다운로드만 된 모델은 자동으로 로드하지 않습니다. Chat 모델을 교체하면 다음
Mem0 호출부터 새 모델을 사용합니다. 설정 검사는 실제 chat 응답과 embedding
벡터를 확인합니다. 기존 `CODEX_MEMORIES`와의 호환성을 위해 embedding은
동일 모델과 1024차원을 유지해야 합니다. 추론 모델은 Mem0의 JSON schema
출력도 지원해야 합니다. 모델 조회는 `/api/v1/models`, 구버전은
`/api/v0/models`를 사용하므로 해당 경로도 API 서버에서 접근 가능해야 합니다.

신뢰한 `Stop` hook은 같은 세션 안에서 Codex가 요청 처리를 마칠 때마다 저장 작업을
대기열에 넣고 백그라운드 워커를 실행합니다. 사용자가 별도로 저장을 요청하거나 Codex가
`save_memory`를 직접 호출할 필요는 없습니다. 워커는 Codex의 진행 내용,
명령·MCP의 입력과 결과, 파일 변경 diff와 최종 응답을 읽습니다. 도구 출력의 중간 부분을
미리 잘라내지 않으며, 같은 호출 ID의 중복 결과는 한 번만 처리합니다. 새 사용자 요청을
작업 경계로 삼고, 조사에서 발견한 사실·선택한 방법과 이유·구체적인 변경·검증과 미확인
사항을 의미에 따라 하나 이상의 vector 기억으로 삽입합니다. 단순한 작업은 하나만 남길
수도 있으며, 모든 도구 호출을 각각 기억으로 만들지는 않습니다. 각 기억에는 문제/증상,
실제 조치, 관찰된 결과와 독립적으로 이해하는 데 필요한 프로젝트·파일 맥락을
항목 제목 없이 본문으로 작성합니다.
`Stop`과 `SessionEnd`의 차이는 [OpenAI 공식 hook 문서](https://learn.chatgpt.com/docs/hooks)를 참고하세요.
전체 작업의 성공·실패·미해결을
분류하는 상태 표시는 저장하지 않으며, 추가 확인이 필요한 사항은 결과 본문에 기술합니다.
`SessionEnd`는 종료 시 누락된 기록을 추가로 처리하고, `SessionStart`는 미처리 작업을 재개합니다.
같은 요청에 대한 반복 `Stop`이나 이후 `SessionEnd`는 체크포인트로 중복 저장을 막습니다.
최종 응답이나 다음 사용자 요청이 아직 없는 작업은 다음 `Stop`·`SessionEnd`까지 기다리며,
긴 작업은 요청 목적·마지막 결과·직전 구간의 원문 일부를 참조 맥락으로 제공하면서,
참조 맥락·메타데이터·JSON 이스케이프를 포함해 직렬화된 입력이 최대 12,000자가 되도록
구간을 나누고 원문에서 직접 기억을 추출합니다.
각 구간에서 나온 기억을 마지막에 하나의 요약으로 다시 합치지 않으며, 동일한 본문의
중복은 제거합니다. 관련 없는 지침·참고 자료만 있는 구간은 기억을 만들지 않으며,
요청 전체의 추출 결과가 비면 완료 처리하지 않습니다. 출력 한도에 도달하거나 모델 서버가
입력 컨텍스트 초과를 보고하면 입력 구간을 줄여 재시도하고, 최소 구간에서도
실패하면 대기열에 남깁니다. 성공한 구간의 기억은 체크포인트로 남겨 다음 실행에서
이어갑니다. 업그레이드 전 중간 요약만 남은 미저장 작업은 원문부터 다시 추출합니다.
기억 metadata에는 세션·요청 식별자, chunk 순서와 개수, 원본 transcript 경로와 요청의
바이트 범위를 남깁니다. 긴 요청에서는 추출 구간의 시작·끝 문자 위치도 남기며, 이 위치는
도구 이벤트를 정규화한 뒤 메시지 content를 이어 붙인 텍스트 기준입니다.
원문 전체가 추출 입력으로 전달되더라도 LLM이 모든 세부 내용을 기억으로 남기는 것은
보장하지 않습니다. 이미 저장한 과거 기억은 자동으로 재추출하지 않습니다.
실패한 작업은 대기열에 남으며 다음 요청 완료·세션 시작·종료나 `mem0-session --drain`에서
재처리합니다. Linux에서 systemd를 사용할 수 있으면 사용자 타이머
`codex-mem0-retry.timer`를 설치하여 로그인 후 1분 뒤, 처리 종료 후 2분마다
재시도합니다. 재부팅 중 종료 훅이 실패해도 다음 로그인에서 대기열을 재개합니다.
다른 플랫폼은 요청 완료·세션 시작·종료 훅으로 재개합니다. 동시에 실행된 워커는 잠금을
확인하고 즉시 종료하여 중복 저장과 워커 누적을 막습니다. 출력 한도 오류 로그에는 원문 대신
처리 단계, 입력 크기와 출력 토큰 수를 기록합니다. 직전 기록은 지시어 해석을 위한
맥락으로만 사용하며 기존 vector를 갱신하지 않습니다. 알려진 토큰·비밀번호
패턴은 마스킹하지만 모든 민감 정보를 완벽히 판별할 수는 없으므로, 민감한 작업에서는 `/hooks`에서 자동
저장을 끄세요.

기존 문서 디렉터리를 가져올 때는 먼저 dry run으로 대상을 확인합니다.
Codex 세션 JSONL도 사용자 요청 안에서 의미별로 문제·조치·결과를 추출해 새 기억으로 삽입합니다.
그 밖의 JSONL과 일반 문서는 기존 사실 추출 방식을
사용합니다. 수동 import의 진행 위치는 별도로 저장되어 같은 세션 파일을 다시
가져와도 완료된 작업을 중복 처리하지 않습니다.

```bash
mem0-import --dry-run /path/to/documents
mem0-import /path/to/documents
```

대화형 경로 선택은 `mem0-import-prompt`를 사용합니다. 이 wrapper는 경로를 입력받은
뒤 import worker를 `nohup`으로 분리합니다. 현재 터미널에는 worker의 진행률 bar와
파일별 최종 처리 결과가 계속 표시되며, 터미널을 닫아도 worker는 계속 실행됩니다.
HTTP 200과 vector insert 같은 정상 진단은 숨기고, 경고·오류만 별도 로그에 기록합니다.

```bash
mem0-import-prompt
tail -f ~/.codex/toolkit/mem0-sessions/manual-import-errors.*
```

각 실행은 PID와 이상 로그 파일을 출력합니다. 처리 상태와 로그는
`~/.codex/toolkit/mem0-sessions/`에 저장됩니다.

## Headroom 모드

Headroom은 MCP 도구로만 사용합니다. `setup.sh`와 `setup.sh --resume`은 기존
Headroom 상주 프록시와 Codex provider 리디렉션을 제거해 OpenAI 직접 연결을 유지하고,
MCP 서버에는 `HEADROOM_MODE=token`과 `HEADROOM_SAVINGS_PROFILE=balanced`를 설정합니다.
큰 로그·검색·JSON 결과는 가능하면 도구 호출 안에서 Headroom으로 압축한 뒤 압축 결과와
복원 hash만 Codex에 반환하도록 전역 정책을 설치합니다. 등록만으로 자동 압축되지는 않습니다.
`headroom_read`도 활성화하여 변경 없는 반복 파일 읽기는 캐시 marker로 처리합니다.
정확한 원문·편집·인용에는 `headroom_retrieve`, 변경 후 읽기에는 `fresh=true`를 사용합니다.

설치와 업데이트는 OS/아키텍처 및 Linux PCI의 GPU 제조사를 감지합니다.
기본 패키지는 `headroom-ai[mcp,code]`, ONNX Runtime, tokenizer용 Transformers이며,
CPU 및 Apple 환경에는 PyTorch/CUDA를 설치하지 않습니다.

| 감지한 환경 | 설치 및 압축 backend |
|---|---|
| Apple Silicon macOS | ONNX CoreML, 실패하면 ONNX CPU |
| Linux AMD / NVIDIA | uv의 자동 호환성 감지 후 해당 ROCm / CUDA PyTorch만 설치 |
| Intel GPU | 제조사는 감지하지만 Headroom의 XPU backend 미지원으로 ONNX CPU |
| GPU 없음 / 드라이버 미지원 / 감지 실패 | ONNX CPU |

GPU 설치나 실제 연산 검증에 실패하면 GPU 의존성을 제외하고 CPU 환경으로 다시 설치합니다.
매번 환경을 재구성하여 이전 `[all]`의 불필요한 의존성을 남기지 않습니다.
선택 결과는 `install-state.json`의 `headroom_hardware`에 기록하며,
설치 중 실제 ML 추론, MCP 압축·정확한 원문 복원, 읽기 캐시를 검증합니다.
모델이 캐시에 없으면 첫 검증에서 Hugging Face 다운로드가 필요합니다.
모델 다운로드에도 Headroom의 OS 인증서 저장소·사용자 CA 설정을 적용합니다.
WSL의 사내 TLS 검사 환경에서도 CA가 WSL에 신뢰 등록되어 있어야 하며,
Hugging Face 클라이언트는 인증서 검증과 offline 모드 검사를 유지합니다.
uv의 `tool install --torch-backend` 지원이 필요합니다. GPU 감지 실패는 CPU로 처리하며,
OS GPU 드라이버 자체는 설치하거나 변경하지 않습니다.
설치 마지막 단계는 활성 MCP의 handshake 및 `codex mcp list`를 실행한 뒤 최종 상태를 출력합니다.

설치된 Headroom의 실제 동작만 재검사하려면 다음을 실행하세요.
backend는 `install-state.json`에 기록된 값(`onnx_cpu`, `onnx_coreml`, `pytorch`)을 사용하고,
PyTorch 환경의 마지막 인자는 기록된 `torch_backend`로 바꿉니다.

```bash
~/.codex/toolkit/uv-tools/headroom-ai/bin/python ~/.codex/toolkit/bundle/headroom_setup.py verify onnx_cpu cpu
```

## 알아둘 점

- Graft는 제외하며, 코드 구조와 호출 관계는 Serena의 심볼·참조 도구를 우선 사용합니다.
- `codex-doctor`는 stdio MCP의 초기화와 도구 목록을 검사합니다. 플러그인과 hook은
  `/mcp`, `/hooks`에서 별도로 확인해야 하며 실제 모델 추론까지 보장하지는 않습니다.
- Remote SSH나 컨테이너에서는 Codex가 실행되는 원격 환경에도 이 툴킷을 설치해야 합니다.
- 이미 설치된 VS Code 확장은 재설치하지 않습니다. 확장 업데이트는 VS Code에서 관리합니다.

### 설치 로그의 경고

| 메시지 | 원인과 처리 |
|---|---|
| uv `--torch-backend` experimental | `uv tool install`의 GPU 선택 옵션에서 발생합니다. toolkit은 기본 환경 설치 후 안정화된 `uv pip install --torch-backend`로 GPU 의존성을 추가합니다. |
| ROCm `(null): No such file or directory` | PyTorch에 포함된 libdrm이 GPU 이름 표 `amdgpu.ids`를 찾지 못할 때 발생할 수 있습니다. Headroom import 전에 기존 표 파일을 찾아 구형용 `AMDGPU_ASIC_ID_TABLE_PATH`에는 파일 경로, 신형용 `AMDGPU_ASIC_ID_TABLE_PATHS`에는 디렉터리를 지정합니다. 사용자가 어느 변수를 지정했든 자동 설정은 생략합니다. 표가 없으면 파일을 만들거나 경고를 숨기지 않습니다. |
| ModernBERT `UNEXPECTED` (`decoder.bias`, `head.dense.weight`, `head.norm.weight`) | 사전학습용 마스크 예측 헤드를 인코더 모델에서 사용하지 않아 발생하는 정상 로딩 보고입니다. Headroom 압축용 가중치는 이후 별도로 로드합니다. 다른 키의 누락·크기 불일치와 추론 오류까지 무시해도 된다는 뜻은 아닙니다. |
| VS Code `DEP0169` | VS Code CLI 내부 `url.parse()` 사용에 대한 Node 경고입니다. 기존 확장의 불필요한 설치 호출은 생략합니다. 새 확장 설치 시 같은 경고가 남으면 VS Code 업데이트가 필요할 수 있습니다. |

`ml_tokens_saved: 0` 하나만으로 모델 실패를 판단하지 않습니다. 입력에 따라 원문을 유지할 수
있으므로, doctor는 실제 추론 점수의 유효성, GPU 실행, MCP 압축·원문 복원·읽기 캐시를
각각 검사합니다. 현재 기기가 GPU를 제공하지 않으면 ROCm 실기 검증은 해당 GPU 기기에서
`headroom_setup.py verify`로 진행해야 합니다.

근거: [PyTorch의 libdrm 패치](https://github.com/pytorch/pytorch/blob/v2.14.1/.ci/docker/common/install_rocm_drm.sh),
[ModernBERT 원본 구성](https://huggingface.co/answerdotai/ModernBERT-base/blob/main/config.json),
[VS Code 경고 보고](https://github.com/microsoft/vscode/issues/326998),
[Codex 인증 안내](https://learn.chatgpt.com/docs/auth).

`e5c37a6`은 구형 변수만 설정하여 신형 libdrm에서 적용되지 않을 수 있었습니다.
수정된 `verify`는 `ROCm ASIC IDs: file=...; directories=...`도 출력합니다.
두 값이 `None`이면 표 파일 부재 또는 사용자 환경 설정을 먼저 확인해야 합니다.
신형 인터페이스의 디렉터리 목록 형식은
[libdrm 변경 내역](https://sourceforge.net/p/dri/mailman/dri-patches/thread/20251022031032.086A9761FB%40kemper.freedesktop.org/?page=1)에서 확인할 수 있습니다.

이미 `e5c37a6`으로 설치를 마쳤다면 저장소에서 다음과 같이 Headroom 실행 파일만 갱신하고
검사할 수 있습니다. 다음 Codex/VS Code 재시작부터 MCP에도 적용됩니다.

```bash
git pull --ff-only
cp headroom_setup.py ~/.codex/toolkit/bundle/headroom_setup.py
~/.codex/toolkit/bin/codex-doctor
```

## 모델 API URL만 재설정

`bash setup.sh --llm`은 기존 toolkit 가상 환경으로 inference primary, inference secondary,
embedding primary, embedding secondary URL을 순서대로 입력받습니다. 각 URL에서 모델 조회와
실제 요청을 검증하고, 실패하면 재입력받습니다. 기존값은 Enter로 유지할 수 있습니다.
네 URL이 모두 검증되면 `~/.codex/mem0.json`의 URL 필드만 저장하며 설치, 로그인,
Oracle 및 다른 도구 설정은 변경하지 않습니다. 저장 후 Mem0/Codex를 재시작하세요.

Mem0는 매 요청에서 primary를 먼저 사용하고 연결 실패·응답 시간 초과·응답 중 연결 단절 시
secondary로 재시도합니다. 각 서버에서 현재 로드된 모델을 사용하며 다음 요청은 다시 primary부터
시도합니다. HTTP 오류 응답과 잘못된 모델 출력은 그대로 오류로 처리합니다.
기존 단일 URL 설정도 primary로 사용할 수 있습니다. 두 embedding 서버는 기존 기억과 호환되는
동일한 embedding 모델과 1024차원 벡터를 제공해야 합니다.

## 개발 검증

설치된 toolkit 가상 환경으로 네트워크 없는 자체 검사를 실행할 수 있습니다.

```bash
python3 check_preflight.py
~/.codex/toolkit/venv/bin/python -m unittest check_headroom
~/.codex/toolkit/venv/bin/python check_setup.py
~/.codex/toolkit/venv/bin/python check_configure.py
~/.codex/toolkit/venv/bin/python check_remove.py
~/.codex/toolkit/venv/bin/python check_mem0_session.py
~/.codex/toolkit/venv/bin/python check_mem0_tasks.py
~/.codex/toolkit/venv/bin/python check_mem0_chunks.py
~/.codex/toolkit/venv/bin/python check_model_api.py
~/.codex/toolkit/venv/bin/python check_llm.py
~/.codex/toolkit/venv/bin/python mem0_import/check_import_memories.py
```
