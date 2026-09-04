# webtoon-server

zip으로 저장해둔 웹툰 회차를 폴더 기반으로 인식해서, 세로 스크롤로 읽을 수 있게 해주는
개인용(셀프호스팅) 웹툰 뷰어입니다. Komga 같은 범용 만화 서버 대신, 웹툰 특유의
"세로 스크롤 + 무한 이어보기 + 기기 간 진행률 동기화"에 맞춰 만들었습니다.

> ⚠️ **이 저장소는 뷰어 소프트웨어만 담고 있습니다.** 웹툰 콘텐츠(zip 파일)는 포함되어 있지
> 않으며, 어디서도 제공하지 않습니다. 각자 정당하게 보유한 콘텐츠로만 사용해주세요.

## 주요 기능

- 폴더 기반 라이브러리 스캔 (플랫폼 폴더 → 시리즈 폴더 → 회차 zip)
- 시리즈 목록 그리드 UI + 검색 + 정렬(최근 업데이트순 / 안읽은 회차 많은순 / 제목순 / 읽음 상태별 필터)
- 각 시리즈 카드에 "다음에 읽을 회차/마지막 회차" 진행률 표시 (다 읽었으면 "완독")
- 시리즈 클릭 시 이전에 읽던 위치(또는 처음 회차)로 바로 이동
- 세로 스크롤 리더: 회차 끝에 도달하면 자동으로 다음 화 이어서 로드(무한 스크롤), 다음 화 프리페치
- 다음 화 맨 앞부분이 이전 화 끝부분과 겹치는(리캡) 페이지를 자동으로 감지해서 건너뜀 —
  건너뛴 지점에는 눈에 잘 안 띄는 작은 버튼이 남아있어 원하면 펼쳐볼 수 있음
- 리더 사이드 패널: 전체 회차 목록 + 현재 보고 있는 회차 + 회차별 읽음/읽는 중 표시
- 목록 화면에서도 회차 목록을 바로 펼쳐볼 수 있는 사이드바 (페이지 이동 없이)
- 회차 단위 읽음/안읽음 수동 표시 (전체 또는 특정 회차 기준으로, 그 회차부터 포함해서 처리)
- 읽음 진행률은 SQLite에 저장되어 여러 기기에서 접속해도 이어보기 가능
- 라이브러리 자동/수동 재스캔 + 마지막으로 스캔한 시각 표시
- 웹 UI에서 시리즈 폴더 스캔 제외/재포함 — 제외해도 실제 폴더/zip 파일은 삭제하지 않음
- 읽음 진행률 + 검색/정렬/필터 설정 + 라이브러리 등록 상태를 통째로 백업/복원
- PWA 아이콘 지원 (아이폰/안드로이드 홈 화면에 추가 가능)
- 외부에서 특정 시리즈의 최신 화 바로가기 URL을 조회할 수 있는 API (알림 봇 등에서 활용 가능)

## 폴더 구조 규칙

```
컨테이너 안 /library
 ├ naver/                 ← docker-compose.yml의 volumes에서 정한 이름 = 화면에 표시되는 태그
 │   └ 어떤 웹툰 제목/       ← 시리즈 폴더 = 웹툰 한 편
 │       ├ 103 ... 100화 - ...zip
 │       └ 104 ... 번외편 ...zip
 └ kakao/
     └ 다른 웹툰 제목/
         ├ 0003_프롤로그#48.zip
         └ 0004_1화#64.zip
```

- 회차 정렬은 zip 파일명 맨 앞 숫자 기준
- 표시 라벨은 파일명에서 "N화"(또는 "제N화") 패턴을 찾아 사용하고, 그 앞에 붙은 부제/시즌
  표시("Extra story", "2부" 등)와 뒤에 붙은 소제목은 함께 보여줌. "N화" 표시가 아예 없으면
  "부제 #번호" 패턴이나 파일명 전체를 그대로 라벨로 사용. 시리즈 폴더명이 파일명 맨 앞에
  그대로 들어있으면 그 부분은 잘라내고, "(完)"·"완결" 같은 완결 표시나 장식성 특수문자는
  정리해서 보여줌.

## 설치

이 저장소는 push할 때마다 GitHub Actions가 이미지를 빌드해서 GHCR에 공개로 올려둡니다.
**fork도 clone도 필요 없습니다.** 아래 내용을 그대로 복사해서, 표시된 줄만 실제 값으로
고친 뒤 Portainer의 **Web editor**에 붙여넣거나 `docker-compose.yml`로 저장해서 CLI에서
`docker compose up -d`로 실행하면 됩니다. 별도로 `.env`나 Portainer의 Environment
variables를 채울 필요가 없습니다 — 이 파일 자체가 곧 스택입니다.

```yaml
services:
  webtoon-server:
    image: ghcr.io/murianwind/webtoon-server:latest
    container_name: webtoon-server
    labels:
      - "com.centurylinklabs.watchtower.enable=true"
    ports:
      - "8000:8000"                                     # <-- 여기 수정: 왼쪽 숫자만 원하는 포트로
    environment:
      - "TZ=Asia/Seoul"                                  # 로그/재스캔 시각을 한국 시간 기준으로
      - "PUBLIC_BASE_URL="                               # <-- 여기 수정(선택): 외부 알림 링크가 필요할 때만 도메인 입력
      - "RESCAN_INTERVAL_SECONDS=7200"                   # <-- 여기 수정(선택): 자동 재스캔 주기(초)
    volumes:
      - "/path/to/your/naver-webtoons:/library/naver"    # <-- 여기 수정: 실제 웹툰 폴더 경로, 오른쪽 이름이 태그
      - "/path/to/your/kakao-webtoons:/library/kakao"    # <-- 여기 수정: 실제 웹툰 폴더 경로, 오른쪽 이름이 태그
      - "webtoon_data:/data"
    restart: unless-stopped

volumes:
  webtoon_data:
```

**`volumes` 한 줄 = 웹툰 사이트 하나.** 콜론(`:`) **왼쪽**은 내 컴퓨터에 실제로 있는
웹툰 폴더 경로, **오른쪽**(`/library/` 뒤)은 화면에 그대로 표시되는 태그 이름입니다.
오른쪽 이름은 완전히 자유롭게 지어도 됩니다.

예를 들어 카카오웹툰을 `D:/Downloads/Webtoon/카카오 웹툰` 폴더에 받아두셨다면:

```
- "D:/Downloads/Webtoon/카카오 웹툰:/library/카카오"
```

이렇게 적으면 화면에 "카카오"라는 태그로 표시됩니다.

사이트가 두 개보다 많거나 적으면, `volumes`에 줄을 자유롭게 추가/삭제하면 됩니다:

```yaml
    volumes:
      - "/path/to/naver-webtoons:/library/naver"
      - "/path/to/kakao-webtoons:/library/카카오"
      - "/path/to/lezhin-webtoons:/library/레진"       # 원하는 만큼 추가
      - "webtoon_data:/data"
```

배포 후 `http://localhost:8000` (또는 위에서 바꾼 포트) 접속.

### 자동 업데이트

위 `labels`에 있는 `com.centurylinklabs.watchtower.enable=true`는 Watchtower용 표시입니다.
**이미 Watchtower를 쓰고 계시면** `--label-enable` 옵션만 켜져 있으면 별다른 설정 없이
자동으로 인식되어, 새 이미지가 올라올 때마다 알아서 pull하고 재시작합니다. Watchtower가
없다면 이 라벨은 그냥 무시되니 지울 필요 없고, 필요할 때 수동으로
`docker compose pull && docker compose up -d` (또는 Portainer의 "Pull and redeploy")만
눌러주면 됩니다.

라이브러리 재스캔은 기본 2시간마다 자동으로 돌고, 바로 반영하고 싶으면 목록 화면
우측 상단 새로고침 버튼(또는 `POST /api/rescan`)을 누르면 됩니다.

## 시리즈 폴더 관리 / 백업

목록 화면 우측 상단의 톱니바퀴(⚙) 버튼을 누르면:

- **스캔 중인 시리즈 폴더**: 플랫폼 폴더 안에서 실제로 스캔되고 있는 웹툰 폴더 목록.
  웹툰이 아닌 폴더가 섞여 들어와 있으면 "제외"를 눌러 스캔 대상에서 뺄 수 있습니다.
  제외해도 실제 폴더나 zip 파일은 절대 지워지지 않고, 목록에서만 빠집니다.
- **제외된 폴더**: 한 번 제외했던 폴더 목록. "다시 추가"를 누르면 바로 시리즈
  목록에 다시 반영됩니다. 방금 옮겨진 항목은 눈에 잘 띄도록 목록 맨 위에 표시됩니다.
- **백업/복원**: 읽음 진행률, 검색/정렬/필터 설정, 제외된 폴더 목록을 JSON 파일
  하나로 내려받거나, 그 파일을 다시 올려서 통째로 되돌릴 수 있습니다. 복원은 현재
  저장된 값을 덮어쓰므로, 실행 전에 확인 창이 한 번 뜹니다.

톱니바퀴 버튼 옆에는 마지막으로 라이브러리를 스캔한 시각이 작게 표시됩니다.

새로운 웹툰 사이트 폴더 자체를 추가하려면(예: 레진을 처음 붙이는 경우) 이 패널이 아니라
`docker-compose.yml`의 `volumes`에 줄을 추가하고 재배포해야 합니다 — Docker 컨테이너는
실행될 때 지정된 경로만 볼 수 있어서, 마운트되지 않은 완전히 새로운 경로는 웹 UI만으로는
추가할 수 없습니다.

## 구글드라이브 / 원드라이브 등 클라우드 드라이브 연결하기

로컬 디스크가 아니라 구글드라이브·원드라이브 같은 클라우드에 웹툰을 보관하고 계셔도,
**[rclone](https://rclone.org)의 공식 Docker 볼륨 플러그인**을 쓰면 코드 수정 없이 그대로
라이브러리에 추가할 수 있습니다. rclone이 지원하는 저장소라면(구글드라이브, 원드라이브,
드롭박스, S3 호환 등) 전부 같은 방법이 통합니다.

**왜 이 방법인가**: WinFsp/rclone mount로 만든 가상 드라이브를 Docker 볼륨으로 직접
연결하려고 하면(Windows + Docker Desktop 조합), 대부분 인식이 안 됩니다 — WinFsp는
Windows가 흉내 낸 가상 파일시스템이라 Docker Desktop의 WSL2 경계를 못 넘는 경우가
흔합니다. SMB 공유로 우회하는 것도 마운트된 드라이브 자체가 공유 조건을 충족 못 하는
경우가 많고요. 반면 아래 방법은 **Docker 자체의 볼륨 플러그인 시스템 안에서 곧바로**
동작하기 때문에 이 경계 문제 자체가 생기지 않습니다.

### 1. rclone 리모트 준비

로컬 PC에 이미 rclone이 설정되어 있다면(`rclone config`로 만든 리모트가 있다면) 그대로
써도 되고, 새로 만들어도 됩니다. 리모트 이름과 그 안의 경로(예: `onedrive:웹툰폴더`)를
알아두세요.

### 2. Docker 볼륨 플러그인 설치

```
docker plugin install rclone/docker-volume-rclone:amd64
```

설치 중 권한(네트워크, 마운트 경로, `/dev/fuse`, `CAP_SYS_ADMIN`) 승인을 물어보면 `y`로
승인합니다.

> 이 단계에서 `open /var/lib/docker-plugins/rclone/config: no such file or directory`
> 같은 에러가 나면, 플러그인이 필요로 하는 폴더가 아직 없어서입니다. 아래처럼 빈
> 컨테이너 하나로 그 경로들을 먼저 만들어준 뒤 다시 설치하면 됩니다:
> ```
> docker run --rm -v /var/lib/docker-plugins/rclone/config:/config -v /var/lib/docker-plugins/rclone/cache:/cache hello-world
> docker plugin install rclone/docker-volume-rclone:amd64
> ```

### 3. rclone 설정 파일을 플러그인에 전달

이 플러그인은 자기만의 별도 설정 공간을 쓰기 때문에, 기존 `rclone.conf`를 그대로
복사해줘야 합니다. `rclone config file` 명령으로 기존 설정 파일 경로를 확인한 뒤:

```
docker run --rm -v "<rclone.conf가 있는 폴더>:/source" -v /var/lib/docker-plugins/rclone/config:/dest alpine cp /source/rclone.conf /dest/rclone.conf
```

### 4. Docker 볼륨 생성

```
docker volume create -d rclone/docker-volume-rclone:amd64 -o "remote=<리모트이름>:<경로>" -o vfs-cache-mode=full <원하는 볼륨 이름>
```

예시 (원드라이브의 "웹툰" 폴더를 `webtoon_onedrive`라는 이름으로):

```
docker volume create -d rclone/docker-volume-rclone:amd64 -o "remote=onedrive:웹툰" -o vfs-cache-mode=full webtoon_onedrive
```

**경로에 공백이 있으면 반드시 `-o "remote=..."` 전체를 따옴표로 감싸세요** — 안 그러면
공백에서 인수가 잘려서 엉뚱한 값으로 인식됩니다.

`vfs-cache-mode=full`은 한 번 읽은 파일을 로컬에 캐싱해서 재접속 시 빠르게 만들어주는
옵션입니다. 필요하면 `-o vfs-cache-max-size=10G`처럼 캐시 용량 상한도 같이 지정할 수
있습니다(디스크 전체를 다 채우지 않도록).

만들어진 볼륨에 실제로 파일이 보이는지는 임시 컨테이너로 확인해보면 됩니다:

```
docker run --rm -v <볼륨 이름>:/test alpine ls /test
```

### 5. `docker-compose.yml`에 연결

최상위 `volumes:`에 이 볼륨을 외부 볼륨으로 등록하고, `webtoon-server` 서비스의
`volumes:`에 마운트를 추가합니다:

```yaml
services:
  webtoon-server:
    # ...
    volumes:
      - "/path/to/your/naver-webtoons:/library/naver"
      - "<볼륨 이름>:/library/<원하는 태그명>:ro"     # <-- 클라우드 드라이브 추가
      - "webtoon_data:/data"
    # ...

volumes:
  webtoon_data:
  <볼륨 이름>:
    external: true
```

`:ro`(읽기 전용)를 붙여두면, webtoon-server가 원래 파일을 안 지우는 서비스이긴 하지만
클라우드 원본 쪽은 이중으로 안전하게 보호됩니다.

재배포하면 이 클라우드 폴더도 다른 플랫폼 폴더와 똑같이 스캔되어 목록에 나타납니다.

### 참고

- 네트워크를 통해 파일을 읽어오는 방식이라, 로컬 디스크보다는 회차를 처음 열 때 느릴 수
  있습니다(캐시된 뒤에는 빨라집니다).
- rclone 설정 파일에는 계정 접근 토큰이 그대로 들어있으니, 다른 사람과 공유하거나
  대화방/채팅에 붙여넣지 않도록 주의하세요.

## API로 최신 화 바로가기 URL 조회하기

디스코드 알림 봇처럼 외부에서 "이 시리즈 최신 화로 바로 가는 링크"가 필요하면
`GET /api/lookup/latest`를 쓰면 됩니다. 컨테이너 안에서(같은 서버의 다른 스크립트 등)
호출한다면 포트로 직접 접근하면 되고, 외부에서 접근한다면 서버 앞단(리버스 프록시 등)의
주소를 쓰면 됩니다.

```
GET /api/lookup/latest?series=<시리즈 폴더명>
GET /api/lookup/latest?series=<시리즈 폴더명>&platform=<플랫폼 폴더명>
```

- **`series`** (필수): 시리즈 폴더명 그대로. 예: `series=마법사랑해`
- **`platform`** (선택): 플랫폼 폴더명. 여러 플랫폼에 우연히 이름이 같은 시리즈가 있어
  구분이 필요할 때만 넘기면 됩니다. 대부분의 경우 생략해도 되고, 그러면 모든 플랫폼을
  통틀어 이름이 일치하는 시리즈를 찾아줍니다. `platform`을 서버의 실제 `/library` 폴더명과
  다르게 넘기면(오타 등) 그 필터 때문에 못 찾을 수 있으니, 웬만하면 생략하는 쪽을
  추천합니다 — 나중에 폴더명을 바꿔도 호출하는 쪽 코드를 고칠 필요가 없어집니다.

예시:

```bash
curl "http://localhost:8000/api/lookup/latest?series=마법사랑해"
```

```json
{
  "series_id": "6b6ad45fcd11",
  "chapter_id": "18b28a0fbd51",
  "chapter_label": "100화 · 아스라이 스러지는 7",
  "url": "https://your-domain.example.com/reader.html?series=6b6ad45fcd11&chapter=18b28a0fbd51&page=0"
}
```

- 일치하는 시리즈가 없으면 `404`
- `url` 필드는 `docker-compose.yml`에 `PUBLIC_BASE_URL`을 설정해둬야 채워집니다.
  설정하지 않았다면 `url`은 `null`로 오고, 나머지 필드(`series_id`, `chapter_id`)로
  직접 링크를 조립하면 됩니다: `<PUBLIC_BASE_URL>/reader.html?series=<series_id>&chapter=<chapter_id>&page=0`

## 라이선스

MIT License — [LICENSE](./LICENSE) 참고. 이 라이선스는 이 저장소의 소스 코드에만 적용되며,
이 소프트웨어로 열어보는 콘텐츠의 저작권과는 무관합니다.
