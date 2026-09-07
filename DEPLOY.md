# 배포 — 네이버 클라우드 마이크로 서버 (Rocky Linux 9.8)

레포: https://github.com/bx2j/classic_search

## 0. 미리 확인

**아웃바운드 인터넷.** NCP는 서버에 공인 IP가 없으면 밖으로 못 나간다
(VPC면 NAT Gateway). KOPIS·예술의전당·롯데·GitHub·슬랙에 전부 나가야 하므로
**공인 IP를 붙이거나 NAT를 둬야 한다.** 공인 IP 요금이 마이크로 서버값보다
클 수도 있으니 콘솔에서 확인할 것.
ACG는 아웃바운드 TCP 80/443만 열려 있으면 된다. 인바운드는 SSH(22)뿐.

**자원.** 메모리 100MB 안팎, 디스크 100MB 미만, 하루 1~2분 실행.
마이크로(1 vCPU / 1GB)로 충분하다.

**파이썬.** Rocky 9의 기본 `python3`는 **3.9**다. 코드가 3.9에서 돌도록
`from __future__ import annotations`를 넣어뒀으므로 **기본 파이썬 그대로 쓰면 된다.**
(3.11/3.12를 쓰고 싶으면 `sudo dnf install -y python3.12` 후 아래 `python3`를 `python3.12`로 바꾼다.)

---

## 1. 서버 기본 세팅

```bash
sudo dnf install -y git python3-pip cronie
sudo systemctl enable --now crond          # 최소 이미지는 crond가 꺼져 있다
sudo timedatectl set-timezone Asia/Seoul   # 중요 - 아래 6번 참고
timedatectl | grep "Time zone"
```

## 2. 코드 내려받기

```bash
cd ~
git clone https://github.com/bx2j/classic_search.git
cd classic_search
```

공개 레포라 인증이 필요 없다.

## 3. 가상환경과 의존성

```bash
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

`truststore`는 설치되지 않는다(윈도우 전용 마커). 리눅스엔 필요 없고,
없으면 `net.py`가 알아서 건너뛴다.

## 4. 비밀정보 넣기

`.env`는 깃에 없다. 서버에서 직접 만든다.

```bash
cp .env.example .env
chmod 600 .env
vi .env
```

```ini
KOPIS_KEY=<KOPIS 서비스키>
SLACK_BOT_TOKEN=xoxb-...
SLACK_CHANNEL=#classic_search
```

`SLACK_CHANNEL`에는 알림 받을 채널명을 `#`까지 포함해 그대로 적는다.
공개 채널이고 봇에 `chat:write.public` 스코프가 있으면 봇을 초대하지 않아도 된다.
비공개 채널이면 그 채널에서 `/invite @봇이름`.

`LOOKAHEAD_DAYS`와 `REGION_PRIORITY`는 비워두면 기본값이 쓰인다.
`SLACK_APP_TOKEN`은 현재 코드가 쓰지 않으므로 비워둔다.

설정이 빠지면 `sync` 실행 시 경고가 뜬다. 슬랙 설정이 없으면 조용히
콘솔로만 출력하므로, 첫 실행 로그에 경고가 없는지 확인할 것.

## 5. 첫 실행

```bash
./run_sync.sh          # 백그라운드 아님. 30분쯤 걸린다
tail -f sync.log
```

첫 실행만 오래 걸린다. 곡목·출연진·예매링크를 1,600여 건 채우기 때문이다.
나눠서 채우고 싶으면:

```bash
.venv/bin/python -m concert_watch sync --limit 200
```

**주의:** DB를 새로 만들면 첫 실행에 현재 매칭 전부(13건)가 한꺼번에 알림으로 간다.
한 번 시끄럽고 마는 거라 그냥 두면 되고, 원치 않으면 6-2를 참고한다.

동작 확인:

```bash
.venv/bin/python -m concert_watch matches
.venv/bin/python -m concert_watch search 브람스 --weekend
```

## 6. cron 등록

```bash
crontab -e
```

```cron
0 9 * * * /home/rocky/classic_search/run_sync.sh
```

경로는 `pwd`로 확인해서 절대경로로 넣는다. `run_sync.sh`가 작업 디렉터리와
`TZ`를 직접 잡으므로 cron 쪽에 추가 설정은 필요 없다.

등록 확인:

```bash
crontab -l
systemctl status crond
```

### 6-1. 시간대가 중요한 이유

수집 구간과 지난 공연 필터가 `date.today()`를 쓴다. 서버가 UTC로 놀면
한국 시간 오전 9시가 UTC 0시라 **하루 어긋난다.** 1번에서 KST를 잡고,
그래도 `run_sync.sh`가 `TZ=Asia/Seoul`을 한 번 더 못박는다.

### 6-2. 기존 DB를 가져오려면

`concerts.db`에 "이미 알린 공연" 기록(`notified`)이 들어 있다.
윈도우 PC에서 옮기면 중복 알림이 없다.

```bash
# 로컬(윈도우)에서
scp D:\workspace\my-project-2\concerts.db rocky@<서버IP>:~/classic_search/
```

## 7. 윈도우 PC 작업 끄기

두 곳에서 같이 돌면 `notified`가 따로 관리돼 **알림이 두 번 간다.**

```powershell
schtasks /delete /tn "concert-watch" /f
```

---

## 갱신

```bash
cd ~/classic_search && git pull && .venv/bin/pip install -r requirements.txt
```

`.env`와 `concerts.db`는 깃에 없으므로 `git pull`이 건드리지 않는다.

## 문제가 생기면

| 증상 | 원인 / 조치 |
|---|---|
| `/usr/bin/env: 'bash\r'` | 셸 스크립트가 CRLF. `.gitattributes`가 막지만, 깨졌다면 `sed -i 's/\r$//' run_sync.sh` |
| `Permission denied` | `chmod +x run_sync.sh` |
| cron이 안 돎 | `systemctl enable --now crond`, 경로가 절대경로인지 확인 |
| 날짜가 하루 밀림 | 시간대. `timedatectl set-timezone Asia/Seoul` |
| 슬랙 안 감 | `.venv/bin/python -m concert_watch slack-test` |
| `TypeError: unsupported operand type(s) for \|` | 파이썬이 3.9 미만. `python3 --version` 확인 |
| 네트워크 타임아웃 | 공인 IP / NAT 미설정. 0번 참고 |
| SELinux 관련 거부 | `sudo ausearch -m avc -ts recent` 로 확인. 홈 디렉터리 실행은 보통 문제없다 |
