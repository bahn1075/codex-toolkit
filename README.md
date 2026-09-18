# Codex Toolkit

macOS와 Linux에서 Codex CLI·VS Code 확장과 개발용 MCP를 한 번에 설치하고,
`~/.codex`의 공통 설정을 관리하는 개인용 설치 번들입니다.

## 설치되는 구성

| 구분 | 구성 요소 |
|---|---|
| Codex | Homebrew Codex CLI, VS Code `openai.chatgpt` 확장 |
| MCP | Serena, Graft, Kubernetes MCP, Context7, Headroom, Mem0 |
| 지침·플러그인 | Ponytail 지침, Superpowers 플러그인 |
| 편의 명령 | `codex-update`, `codex-doctor`, `mem0-import` |

Mem0는 Oracle AI Vector Search와 Tailscale의 `mac` 모델 서버를 사용합니다.
Headroom은 기본적으로 로컬 프록시를 상주 실행하며, 사용할 수 없는 환경에서는
MCP 전용 모드로 전환할 수 있습니다.

## 설치 전 확인

> [!WARNING]
> 기본 설치는 기존 `~/.codex`를 `~/.codex-backups/reset-*`으로 옮긴 뒤 새로
> 구성합니다. 기존 로그인·대화·전역 설정이 새 환경에 자동 병합되지는 않습니다.
> 프로젝트 안의 `.codex/config.toml`과 `AGENTS.md`는 건드리지 않습니다.

- 지원 환경: macOS 또는 Linux, Bash 3.2 이상
- 사전 명령: Homebrew, Node.js 22 이상, npm, uv, git, python3, Bun
- Graft 빌드 도구: C/C++ 컴파일러와 make(macOS는 Xcode Command Line Tools)
- 설치 디렉터리는 `~/.codex` 밖에 두고 일반 사용자로 실행합니다. `sudo`를 쓰지 마세요.
- 실행 중인 Codex CLI, Codex 앱, VS Code를 모두 종료하세요.

## 셸 스크립트 사용법

| 명령 | 용도 |
|---|---|
| `bash setup.sh` | 최초 설치 또는 완전 초기화. 기존 `~/.codex`를 백업한 뒤 새로 구성 |
| `bash setup.sh --resume` | 기존 `~/.codex`를 유지하면서 실패·누락된 설치와 설정을 다시 맞춤 |
| `bash update.sh` | 설치된 툴킷과 패키지를 업데이트하고 설정을 재적용 |
| `bash setup.sh --help` | 지원 옵션과 환경 변수 확인 |

`setup.sh`와 `update.sh`는 다음 작업을 순서대로 수행합니다.

1. 운영체제, 필수 명령, Node.js 버전과 실행 중인 Codex 프로세스를 검사합니다.
2. Codex와 MCP 의존성을 설치하거나 업데이트하고, 등록된 Serena 프로젝트의 언어 서버를 소스 구성에 맞게 다시 감지합니다.
3. `~/.codex/config.toml`, 전역 `AGENTS.md`, shell alias를 구성합니다.
4. Mem0 연결 정보와 hook, Headroom 실행 모드, Superpowers를 구성합니다.
5. 설정 유효성과 Graft MCP의 실제 시작·도구 목록을 검사합니다.

### 최초 설치

```bash
bash setup.sh
```

설치 중 Mem0 연결에 필요한 Oracle wallet 디렉터리, DB 계정, TNS alias와 wallet
비밀번호를 입력합니다. 비밀값은 권한 `600`인 `~/.codex/mem0.json`에만 저장됩니다.

완료 후 새 터미널을 열고 다음 순서로 확인합니다.

```bash
codex login
codex-doctor
codex
```

Codex의 `/mcp`에서 연결 상태를 확인하세요. 자동 메모리를 사용하려면 `/hooks`에서
Mem0의 `SessionStart`·`SessionEnd` hook을 검토하고 신뢰해야 합니다.

정상 완료된 `setup.sh`는 Mem0 구성과 Graft 활성화·검증까지 마친 상태이므로
`--resume`을 다시 실행할 필요가 없습니다.

Superpowers는 로그인이나 워크스페이스 관리자 허용 문제로 설치가 실패할 수 있습니다.
나머지 설치는 계속되며, 조건이 해결된 뒤 다음 명령으로 다시 설치할 수 있습니다.

```bash
codex plugin add superpowers@openai-curated-remote
```

### `--resume`의 역할

`--resume`은 실패한 한 줄에서 그대로 이어가는 옵션이 아니라, **초기화를 생략하고
설치 전체를 다시 조정하는 복구 모드**입니다.

- `~/.codex`를 이동하거나 새로 만들지 않아 로그인, 대화 기록과 사용자 설정을 유지합니다.
- 설치 상태를 재사용하면서 패키지, MCP 실행 경로, 전역 지침과 hook을 다시 확인합니다.
- Mem0 설정 파일이 없으면 Oracle 연결 정보를 다시 입력받고, 있으면 기존 값을 재사용합니다.
- Graft 실행 경로를 다시 등록하고 임시 Git 저장소에서 MCP 시작과 도구 제공을 검증합니다.
- 모든 설치 단계를 다시 실행하므로 네트워크가 필요하고 다소 시간이 걸릴 수 있습니다.

다음 경우에 실행하세요.

- 최초 설치가 오류나 강제 종료로 중단된 경우
- `Partial setup`이 표시된 경우
- Mem0 입력을 완료하지 못했거나 hook/MCP 등록을 다시 적용해야 하는 경우
- Node, npm, uv, kubeconfig 등 실행 환경의 경로가 바뀐 경우
- Headroom 모드를 바꾸거나 Graft 설정을 복구한 경우

```bash
bash setup.sh --resume
```

원본 설치 디렉터리가 없다면 설치 중 복사된 스크립트를 사용합니다.

```bash
bash ~/.codex/toolkit/bundle/setup.sh --resume
```

아직 한 번도 설치하지 않아 설치 상태나 초기 백업 표식이 없으면 `--resume`은 사용할
수 없습니다. 이 경우 `bash setup.sh`로 시작해야 합니다.

### 설치 후 Mem0 설정 완료

최초 설치가 Mem0 단계 전에 멈췄거나 `~/.codex/mem0.json`이 아직 없다면
`--resume`을 실행해 Oracle 설정 입력과 MCP·hook 등록을 완료해야 합니다.

```bash
bash ~/.codex/toolkit/bundle/setup.sh --resume
codex-doctor
```

기존 `mem0.json`이 있으면 `--resume`은 그 값을 덮어쓰거나 다시 묻지 않습니다.
연결 정보 자체를 바꿀 때는 권한 `600`을 유지하며 해당 파일을 먼저 수정한 뒤
`--resume`을 실행하세요. 완료 후 Codex를 다시 시작해 `/mcp`에서 `mem0`,
`/hooks`에서 두 hook을 확인합니다.

### Graft 활성화와 확인

최초 설치와 이전 설정의 마이그레이션에서는 Graft를 기본 활성화합니다. 이후 사용자가
`~/.codex/config.toml`의 기존 `[mcp_servers.graft]` 항목을
`enabled = false`로 바꾼 경우에는 `--resume`도 그 선택을 보존합니다. 다시
활성화하려면 같은 항목을 `enabled = true`로 수정한 뒤 다음을 실행하세요.

```bash
bash ~/.codex/toolkit/bundle/setup.sh --resume
codex mcp list
codex-doctor
```

Codex를 Git 저장소 안에서 다시 시작하고 `/mcp`에서 `graft`가 활성 상태인지
확인합니다. Git 저장소 밖에서 Graft가 도구 0개로 대기하는 것은 정상입니다.
Codex CLI와 IDE 확장은 같은 MCP 설정을 공유합니다.
[공식 MCP 안내](https://developers.openai.com/codex/mcp)

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

신뢰한 hook은 세션 종료 후 사용자 메시지와 최종 응답에서 장기적으로 유용한 사실만
추출해 저장합니다. 알려진 토큰·비밀번호 패턴은 마스킹하지만 모든 민감 정보를 완벽히
판별할 수는 없으므로, 민감한 작업에서는 `/hooks`에서 자동 저장을 끄세요.

기존 문서 디렉터리를 가져올 때는 먼저 dry run으로 대상을 확인합니다.

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

기본 `proxy` 모드는 `127.0.0.1:18787`의 상주 프록시를 사용합니다. 프록시를 쓰지
않으려면 MCP 전용 모드로 전환합니다.

```bash
CT_HEADROOM_MODE=mcp bash ~/.codex/toolkit/bundle/setup.sh --resume
```

다시 상주 프록시로 전환하려면 `mcp`를 `proxy`로 바꿔 같은 명령을 실행합니다.
프록시 구성에 실패하면 Codex 직접 연결을 복구하고 설치를 부분 실패로 표시합니다.

## 알아둘 점

- Graft는 Git 저장소에서만 구조 인덱스를 자동 준비합니다. 별도의 `graft init`이나
  `graft build`는 필요하지 않습니다.
- Kubernetes MCP는 기존 kubeconfig와 `current-context`를 사용하며 클러스터를 자동으로
  만들거나 바꾸지 않습니다.
- Context7 API 키가 필요하면 설치 또는 `--resume` 실행 시 `CONTEXT7_API_KEY`를
  환경 변수로 전달하세요.
- `codex-doctor`는 stdio MCP의 초기화와 도구 목록을 검사합니다. 플러그인과 hook은
  `/mcp`, `/hooks`에서 별도로 확인해야 하며 실제 모델 추론이나 클러스터 권한까지
  보장하지는 않습니다.
- Remote SSH나 컨테이너에서는 Codex가 실행되는 원격 환경에도 이 툴킷을 설치해야 합니다.

## 개발 검증

설치된 toolkit 가상 환경으로 네트워크 없는 자체 검사를 실행할 수 있습니다.

```bash
~/.codex/toolkit/venv/bin/python check_configure.py
~/.codex/toolkit/venv/bin/python check_graft.py
~/.codex/toolkit/venv/bin/python check_mem0_session.py
~/.codex/toolkit/venv/bin/python mem0_import/check_import_memories.py
```
