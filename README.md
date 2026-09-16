# Codex 통합 설치 스크립트

작성 기준: 2026-09-15. macOS와 Linux에서 동일하게 `bash setup.sh`를 실행합니다.
수정: OS 인증서 저장소를 사용하는 `UV_SYSTEM_CERTS=true`를 기본 적용하고,
tomlkit 설치 중 실패하여 상태 파일이 아직 없는 경우에도 `--resume`을 지원합니다.
Python 3.13은 Codex 자체의 필수 조건이 아니라 Serena·Headroom 안내에 맞춘 도구 전용
환경 선택입니다. 기본 `python3`가 3.14여도 그대로 두고 두 버전을 함께 사용합니다.
이 파일은 사용자 컴퓨터에서 실행할 설치 스크립트의 설명입니다. 파일 제공 시점에
사용자 컴퓨터의 설정을 변경하거나 도구를 설치한 것은 아닙니다.

## 사용 순서

압축을 풀고 `codex-toolkit` 디렉터리에서 실행합니다. 설치 파일은 `~/.codex` 밖에
두세요. 실행 중인 Codex CLI, Codex 앱, VS Code는 먼저 종료합니다.
Homebrew와 npm/Node.js, uv, git, python3, Bun이 설치되어 있어야 합니다.
이 번들은 Node.js 22 이상, Bash 3.2 이상을 사용합니다. sudo는 사용하지 않습니다.

```
bash setup.sh
```

설치 후 새 터미널을 열고 로그인합니다. 이미 열린 부모 셸의 alias는 자식 설치
스크립트가 바꿀 수 없으므로 새 터미널이 필요합니다. 이것은 최초 설치 시 한 번입니다.

```
codex login
codex
```

Codex에서 `/mcp`를 열어 `mem0`이 연결되었는지 확인하고 새 대화를 시작합니다.
Mem0 MCP 도구는 바로 사용할 수 있습니다. 세션 종료 시 자동 저장도 사용하려면
`/hooks`에서 설치된 Mem0 `SessionStart`·`SessionEnd` hook을 검토하고 신뢰하세요.

이후에는 평소처럼 프로젝트에서 `codex`를 실행하거나 VS Code의 **Codex 확장**을
열면 됩니다. `use context7`, Serena 활성화 요청, 별도의 프록시 시작 명령을 매번
입력할 필요가 없도록 설정합니다. `config.toml`은 CLI·IDE 공통 설정이고,
`AGENTS.md`는 Codex가 자동으로 읽는 사용자 지침입니다.
[OpenAI MCP 문서](https://developers.openai.com/codex/mcp),
[AGENTS.md 문서](https://developers.openai.com/codex/guides/agents-md)

## 도구별 설치와 적용

| 대상 | 설치 방법 | 적용 방식 / 범위 |
|---|---|---|
| Codex | `brew install --cask codex` | 기존 설치 제거 후 재설치, 이후 업데이트는 cask upgrade |
| Serena | `uv tool install --python … serena-agent` | 전역 stdio MCP, 대시보드·브라우저 자동 열기 비활성화, 현재 작업 프로젝트 활성화는 Codex가 처리 |
| Ponytail | 공식 Git 저장소 clone | 배포 저장소의 `AGENTS.md`를 전역 `AGENTS.md`의 관리 블록에 반영 |
| Superpowers | `codex plugin add superpowers@openai-curated-remote` | 설치·업데이트 시 공식 플러그인 설치 시도. 로그인 및 워크스페이스 관리자 허용 필요 |
| Graft | private npm prefix의 `@nanonets/graft` | MCP 등록, 신규 설치 시 기본 비활성화. Git 프로젝트에서 활성화하거나 CLI 사용 |
| Mem0 | 격리된 toolkit venv의 `mem0ai` + 로컬 MCP | Oracle AI Vector Search와 Tailscale `mac` 모델 서버를 사용 |
| Headroom | 격리된 uv의 `headroom-ai[all]` | 전역 MCP + 기본값으로 상주 프록시 배포 |
| Kubernetes MCP | `brew install kubernetes-mcp-server` | 전역 MCP, 기존 kubeconfig와 인증 플러그인 사용 |
| Context7 | private npm prefix의 `@upstash/context7-mcp` | 전역 stdio MCP, 문서 작업에 자동 사용하도록 지침 설정 |

Superpowers 설치가 실패해도 나머지 툴킷 설치는 계속합니다. 신규 설치 후에는
`codex login`을 수행하고, 관리자 차단(`Disabled by admin`)이 있다면 허용된 뒤
`codex plugin add superpowers@openai-curated-remote`로 재시도하세요.
설치 후 새 Codex CLI 세션에서 사용합니다. 현재 공식 문서상 IDE 확장은 플러그인을
지원하지 않습니다. [플러그인 문서](https://developers.openai.com/codex/plugins/)

현재 공식 Homebrew Codex cask 페이지에는 macOS와 Linux 배포가 모두 명시되어 있습니다.
Homebrew가 오래되어 플랫폼을 지원하지 않는 경우에는 설치를 실패로 보고하며,
임의의 비공식 tap이나 이름이 같은 다른 패키지로 바꾸지 않습니다.
[Codex cask](https://formulae.brew.sh/cask/codex),
[Kubernetes MCP formula](https://formulae.brew.sh/formula/kubernetes-mcp-server)

나머지 항목은 확인한 공식 배포 방식에 따라 uv/npm/Git을 사용합니다. 이름이 비슷한
Homebrew 패키지를 추측하여 설치하지 않습니다. uv·npm 도구 본체는 가능한 한
`~/.codex/toolkit/` 안에 격리합니다. Python 3.13은 Homebrew로 설치하고, TOML을
보존하며 수정하기 위한 `tomlkit`만 별도 작은 venv에 설치합니다.

Ponytail은 사용자가 요청한 **md 자동 적용 방식**입니다. upstream의 최신 지침
파일을 그대로 가져오며 plugin 전용 slash command, 레벨 전환 hook까지 설치하는
방식은 아닙니다. 업데이트하면 upstream 지침 블록도 갱신됩니다.
[Ponytail 공식 설명](https://github.com/dietrichgebert/ponytail)

## Mem0: Oracle AI Vector Search 메모리

설치 중 Oracle wallet 디렉터리, DB 사용자명·비밀번호, wallet의 TNS alias와 wallet
비밀번호를 묻습니다. 자동 로그인 wallet이면 wallet 비밀번호는 비워 둡니다.
설정은 권한 `600`인 `~/.codex/mem0.json`에만 저장되며 install-state·명령행·로그에는
비밀값을 기록하지 않습니다. 기존 설정이 있으면 `--resume`과 업데이트에서는 재입력하지
않습니다.

Mem0는 Oracle AI Vector Search provider가 포함된 upstream commit과 `CODEX_MEMORIES`
collection을 사용합니다. 추론은
`http://mac.tail651fca.ts.net:1234/v1`의 `qwen3.8-27b-mlx`, 임베딩은 같은 서버의
`text-embedding-bge-m3`(1024차원)로 고정합니다. 서버와 Oracle 연결이 모두 가능해야
저장·검색 도구가 동작합니다. Codex에서는 `/mcp`의 `mem0` 도구로 메모리를 저장·검색·목록·삭제합니다.
[Mem0 공식 저장소](https://github.com/mem0ai/mem0)

설치·업데이트는 `~/.codex/hooks.json`에 Mem0 hook을 병합하며 다른 hook을 보존합니다.
`SessionEnd`는 종료 시점의 대화 기록 범위를 큐에 기록하고 별도 프로세스를 시작합니다.
Codex 종료 hook은 최대 3초이므로 실제 모델 추론·Oracle 저장은 종료 이후에도 계속됩니다.
`SessionStart`는 이전에 실패하거나 중단된 큐만 재시도합니다.

- 신규 세션: 사용자 메시지와 최종 응답에서 장기적으로 유용한 사실을 추출해 저장합니다.
- resume: 동일 세션 ID의 마지막 성공 지점 이후만 처리합니다. 새 대화가 없으면 저장하지 않습니다.
- 저장 실패: 완료 지점을 갱신하지 않고 다음 시작·종료 또는 수동 실행에서 재시도합니다.
- 저장 직후 중단: Oracle에 기록한 사실 식별자를 확인해 재삽입을 방지합니다.

기록은 기본 `codex` 메모리 영역에 들어갑니다. 도구 출력·내부 추론·시스템 지침은
제외하며, 알려진 토큰·비밀번호 패턴은 마스킹합니다. 자유 형식의 모든 비밀값을
완벽하게 식별하는 필터는 아니므로 민감한 세션은 `/hooks`에서 자동 저장 hook을 끄세요.
Mem0의 LM Studio provider와 JSON schema를 사용하며, 추출 JSON이 잘못되면 성공으로
처리하지 않습니다.

큐·추출 대기 데이터·완료 지점은 `~/.codex/toolkit/mem0-sessions/`에 저장됩니다
(디렉터리 700, 데이터 파일 600). `worker.log`에서 성공·실패를 확인할 수 있습니다.
`OK` 뒤의 `messages`는 읽은 대화 수, `llm_calls`는 사실 추출 호출 수,
`inserted`는 Oracle 저장 확인 건수입니다. 모두 0이면 모델 호출·신규 저장 없이
완료된 작업입니다. 이전 `user_message`/`agent_message`와 현재
`item_completed`의 `UserMessage`/`AgentMessage` 기록 형식을 지원합니다.
실패 원인을 수정한 뒤 수동 재시도할 수도 있습니다:

```bash
~/.codex/toolkit/bin/mem0-session --drain
```

완료 지점은 이 hook으로 처리한 대화부터 추적합니다. 처음 도입할 때 기존 세션의
수동 MCP 저장 여부는 역산할 수 없으므로, 완료 지점이 없는 세션은 전체 기록을 한 번
처리합니다. 정상적인 `SessionEnd`가 발생하지 않는 강제 종료에서는 새 작업이 등록되지
않으며, 해당 세션을 resume한 뒤 정상 종료하면 아직 처리하지 않은 부분을 처리합니다.
Codex JSONL transcript 형식은 안정된 공개 API가 아니므로 버전 변경 시 확인이 필요합니다.
[Codex 공식 hook 문서](https://developers.openai.com/codex/hooks)

## Headroom: 매번 시작하지 않는 구성

기본 `CT_HEADROOM_MODE=proxy`는 공식 `headroom deploy --no-docker`에 Codex만 대상으로
지정합니다. 프로필 이름은 `codex-toolkit`, 로컬 포트는 `18787`입니다. 지원되는
native supervisor를 통해 자동 복구·시작하도록 설치하고, Codex의 provider 설정을
수정합니다. 설치 당시 아직 로그인하지 않았으므로 ChatGPT OAuth 사용을 명시하는
`requires_openai_auth = true`를 설정합니다. 이 번들의 기본 인증 방식은 ChatGPT입니다.

공식 installer는 지원되는 launchd/cron 등 host 환경을 선택합니다. 커널 버전만으로
상주 서비스 가능 여부가 결정되지는 않습니다. supervisor를 구성하지 못하거나 health
확인이 실패하면 직접 연결 설정으로 복구하고 전체 설치 결과를 부분 실패로 표시합니다.
프록시를 사용할 수 없더라도 Headroom MCP는 등록되어 있습니다. 이 상태는 자동 압축이
완료된 상태가 아닙니다. 실행 중 프록시 장애에 대한 Codex의 자동 우회까지 구현한
것은 아니므로, 그 경우 resume로 복구하거나 아래 MCP 전용 설정으로 전환합니다.

```
CT_HEADROOM_MODE=mcp bash ~/.codex/toolkit/bundle/setup.sh --resume
```

MCP 전용 모드는 프록시를 제거하고 일반 ChatGPT 연결을 사용합니다. 압축 도구를
호출할 수 있지만 다른 MCP의 결과를 자동 가로채지는 않습니다.
이후 proxy 모드 복구는 같은 명령에서 `mcp`를 `proxy`로 바꿉니다.
[상주 배포 문서](https://github.com/headroomlabs-ai/headroom/blob/main/docs/content/docs/persistent-installs.mdx),
[Codex OAuth 처리](https://github.com/headroomlabs-ai/headroom/blob/main/headroom/providers/codex/install.py)

## 프로젝트·VS Code·Kubernetes 동작 범위

Hook 설정은 `[features]`의 `hooks = true`를 사용합니다. 설치·resume·업데이트 시
폐기된 `codex_hooks` 키를 제거합니다.

Graft는 신규 설치에서 전역 `enabled = false`로 등록합니다.

설치·resume·업데이트는 private npm prefix에 Graft 파서의 설치 스크립트 허용 목록을
기록하고 네이티브 모듈을 재빌드합니다. npm 12의 기본 스크립트 차단으로 컴파일이
누락되는 경우를 방지하며, 설치에 선택한 Node를 MCP 실행에도 사용합니다.
완료 전에는 활성화 여부와 관계없이 임시 Git 저장소에서 Graft MCP 초기화와 도구
목록 조회를 검사합니다. 빌드 또는 MCP 검사가 실패하면 설치는 실패로 종료되며,
출력된 원인을 해결한 뒤 `bash setup.sh --resume`으로 재개할 수 있습니다.
소스 빌드가 필요한 환경에는 C/C++ 컴파일러와 make(macOS는 Xcode Command Line
Tools), Node 헤더 다운로드에 필요한 네트워크/인증서 설정이 필요합니다.
설치 후 Node나 패키지를 별도로 변경한 경우에는 업데이트를 다시 실행하세요.

기존에 지정한 활성화 설정은 업데이트 시 보존합니다. 이전 설치에서 Git 저장소 밖의 시작 경고를 없애려면
`~/.codex/config.toml`의 `[mcp_servers.graft]`에 `enabled = false`를 설정하세요.
실제 Git 프로젝트의 신뢰된 `.codex/config.toml`에는 다음 설정을 추가할 수 있습니다.

```toml
[mcp_servers.graft]
enabled = true
```

CLI에서 해당 Git 프로젝트에 한 번만 활성화하려면
`codex -c mcp_servers.graft.enabled=true`를 실행합니다.

Graft 0.18.0은 인덱스가 없는 저장소에서 `connected (0 tools)`로 표시되는 것이
정상입니다. 도구를 사용하려면 해당 저장소에서 `graft build`로 구조 인덱스를
생성한 뒤 Codex 세션을 다시 시작하세요. `graft init`이나 유료 `--deep` 빌드는 필요하지 않습니다.

Kubernetes 설치 검증과 doctor는 `kubectl config view`로 로컬 kubeconfig의
`current-context`를 확인합니다. 비어 있거나 유효하지 않으면 context 목록과 해결
안내를 출력합니다. 사용할 대상을 확인한 뒤 같은 `KUBECONFIG` 환경에서
`kubectl config use-context <선택한-context>`를 실행하세요. 자동 선택이나 클러스터
접속은 하지 않습니다. 사용자 지정 MCP 인수가 있거나 kubectl이 없으면 이 사전
검사는 건너뛰고 doctor의 실제 MCP 시작 검사로 확인합니다.

- Serena는 하나의 프로젝트로 고정하지 않습니다. Codex가 현재 작업 디렉터리를 확인한
  후 `activate_project` 등을 사용하도록 전역 지침에 명시합니다. 언어별 추가 런타임이나
  language server가 필요하면 해당 프로젝트에서 별도로 설치해야 할 수 있습니다.
- Graft의 MCP 서버는 시작 디렉터리와 연결됩니다. Git 저장소 밖에서 시작하면 홈 전체를
  인덱싱하지 않고 종료합니다. IDE가 MCP를 저장소 밖에서 시작했을 때는 Codex가 실제
  프로젝트 디렉터리에서 Graft CLI를 사용하도록 지침에 명시합니다. 따라서 모든 IDE
  실행 방식에서 Graft가 반드시 MCP로 동작한다는 보장은 없습니다.
- Graft의 기본 구조적 그래프는 별도 API 키 없이 만들 수 있습니다. LLM 기반 `--deep`
  보강은 기본 자동 작업에 넣지 않았습니다. 필요할 때 별도의 공급자 설정이 필요합니다.
- VS Code의 일반 Copilot MCP 설정이 아니라 **Codex 확장 설정**을 사용합니다.
  `code` 명령이 있으면 공식 확장 `openai.chatgpt` 설치·업데이트를 시도합니다.
  `code`가 없으면 VS Code에서 기존 Codex 확장을 업데이트한 뒤 재시작하세요.
- Remote SSH/컨테이너에서는 Codex가 실행되는 원격 머신에도 설치해야 합니다. 로컬
  Mac의 `.codex`가 원격 Linux에 자동 복제되지는 않습니다. VS Code에서 사용자 지정
  Codex executable 경로나 별도 profile을 지정했다면 해당 설정도 확인해야 합니다.
- MCP 실행 파일은 절대경로로 등록하고, GUI 실행 때도 사용할 런타임 PATH를 기록합니다.
  nvm 등의 Node 버전 디렉터리를 삭제했거나 OCI/AWS 인증 플러그인 경로를 변경한 경우에는
  resume를 실행해 갱신합니다. 런타임과 인증 helper는 실제 머신에 존재해야 합니다.
- Kubernetes MCP가 읽는 kubeconfig를 새로 만들거나 cluster를 변경하지 않습니다.
  실행 당시 `KUBECONFIG`가 명시되어 있으면 그 **파일 경로**를 저장합니다. 민감한
  kubeconfig 내용을 전역 md에 복사하지 않습니다. 읽기 전용으로 강제하지 않으므로
  사용 가능한 변경 작업은 기존 Kubernetes 권한과 Codex 승인 정책에 따릅니다.
- Context7은 키 없이 시도하며 인증/사용량 제한이 있을 수 있습니다. 필요하면 설치·resume
  환경의 `CONTEXT7_API_KEY`를 전달하여 전용 설정에 저장할 수 있습니다.

[Serena 연결 문서](https://oraios.github.io/serena/02-usage/030_clients.html),
[Graft](https://github.com/trailhq/Graft),
[Kubernetes MCP](https://github.com/containers/kubernetes-mcp-server),
[Context7](https://github.com/upstash/context7)

## 초기화·보존 범위

`setup.sh` 기본 실행은 `~/.codex`를 `~/.codex-backups/reset-날짜-임의문자/codex`로
옮긴 뒤 새 디렉터리를 만듭니다. 이전 설정, 세션, 사용자 md를 새 설정에 섞지 않으며,
백업 자체는 삭제하지 않습니다. 따라서 백업에는 이전 인증 정보가 포함될 수 있습니다.

현재 활성 npm prefix의 `@openai/codex`와 기존 Homebrew Codex를 제거합니다.
다른 nvm prefix, 사용자 작성 shim, 임의 디렉터리의 바이너리까지 추측하여 지우지는
않습니다. 새 셸에서는 `codex` alias가 Homebrew 바이너리를 직접 지정합니다.
이는 프록시 실행용 alias가 아니며 IDE는 alias와 무관하게 공통 설정을 읽습니다.

`.zshrc`(ZDOTDIR 반영), `.bashrc`, `.bash_profile`에는 식별 가능한 관리 블록을
추가하고 기존 파일을 백업합니다. 이 번들의 셸 자동 적용 대상은 bash와 zsh입니다.
기존 프로젝트의 `.codex/config.toml`, `AGENTS.md`와 조직 정책은 삭제하지 않습니다.
그 설정이 전역 구성을 재정의할 수 있습니다. MCP 우선 md 지침은 모델 행동 지침이지
운영체제 명령 실행을 강제로 차단하는 정책은 아닙니다.

## 업데이트와 장애 복구

```
bash ~/.codex/toolkit/bundle/update.sh
```

또는 새 터미널에서 `codex-update`를 실행합니다.

| 설치 경로 | 업데이트 동작 |
|---|---|
| Homebrew | Codex cask, Kubernetes MCP formula만 `brew upgrade` |
| 격리 uv | `uv tool install --upgrade`로 Serena·Headroom 갱신 |
| private npm | 지정한 세 package의 `@latest` 재설치, package-lock과 버전 기록 |
| Ponytail Git | 로컬 변경 확인 후 `git pull --ff-only`, md 관리 블록 갱신 |
| Mem0 | `mem0ai`와 MCP 의존성 갱신, 기존 Oracle 설정은 유지 |
| Headroom runtime | 이 번들의 프로필만 재생성하여 새 버전 프로세스 구동 |
| VS Code | `code`가 있으면 Codex 확장 재설치/업데이트 |

업데이트는 로그인·대화 기록을 초기화하지 않습니다. 설정·지침의 사용자 추가 부분도
보존합니다. 관리되는 도구 실행 경로와 upstream Ponytail 블록은 갱신됩니다.
처리 중 오류가 나면 전체 완료로 표시하지 않습니다. dependency 단계 실패는 중단하고,
독립 연동 단계 실패는 기록한 후 부분 실패로 종료합니다. 전체 자동 롤백은 아니므로
오류를 수정한 후 `setup.sh --resume`로 재실행합니다.

`update.sh`는 설정 백업을 `~/.codex-backups/update-*`에 남깁니다. 완전한 초기
설정 복구가 필요하면 먼저 이 번들의 Headroom 프로필을 제거하고 Codex를 종료한 뒤,
새 `.codex`를 별도로 보관하고 초기 백업 디렉터리를 `.codex`로 복원하세요. 이는
Homebrew/npm 바이너리 버전이나 외부 Oracle Mem0 데이터까지 되돌리는 롤백은 아닙니다.
셸의 추가 관리 블록도 이전 셸 파일 백업으로 되돌릴 수 있습니다.

## 확인 방법과 테스트 한계

프로젝트 디렉터리에서 다음을 실행합니다.

```
codex-doctor
```

일반 stdio MCP 서버의 실제 initialize/tools-list 프로토콜을 확인합니다. cluster 변경
도구를 호출하지 않습니다. plugin 제공 MCP와 hook은 Codex의 `/mcp`, `/hooks`에서도
확인하세요. 점검 과정에서도 언어 서버 초기화 등 upstream의 정상 시작 작업은 발생할
수 있습니다. `/mcp`에 이름이 보이는 것만으로 성공적인 실제 도구 호출을 증명하지는
않습니다.

로그인 후 검증할 요청 예시:

```
현재 저장소의 구조를 설명하고, 사용한 MCP 도구 이름을 알려줘.
현재 Kubernetes context의 namespace 목록을 MCP로 조회해줘.
이 프로젝트의 프레임워크 버전에 맞는 공식 사용법을 Context7으로 확인해줘.
```

메모리는 한 세션에서 작업한 뒤 새 세션에서 관련 기록을 검색하고, 실제 관찰 기록이
생성되었는지 확인해야 합니다. Headroom은 로그인 후 실제 요청을 보낸 뒤 로컬
dashboard/stats에서 트래픽 통과를 확인해야 합니다. health 응답만으로 OAuth 중계나
압축 효과까지 검증한 것은 아닙니다.

제공 환경에서는 네트워크 제한으로 실제 패키지 설치와 macOS 실행을 수행하지 못했습니다.
검증 범위는 셸/Python 문법, md 블록 갱신, 공백·인용부호 경로 처리, 격리된 가짜 실행
파일을 사용하는 백업·MCP 프로토콜 점검입니다. `tomlkit` 설치도 불가능하여 실제
TOML 병합·직렬화 실행은 검증하지 못했습니다. 실제 사용자 머신에서 7개 최신 패키지가 모두 설치·연동되었다고 보장하는
검증 완료 배포본은 아닙니다. upstream CLI/설정 변경은 실행 시 오류로 드러날 수 있습니다.
