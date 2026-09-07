# 네이버 클라우드 마이크로 서버에 올리기

결론부터: **돕니다.** 윈도우 의존 코드가 없고, 자원도 거의 안 쓴다.

## 자원 요구량

| | 필요 | 마이크로 서버(1 vCPU / 1GB) |
|---|---|---|
| 메모리 | 100MB 안팎 | 여유 |
| 디스크 | DB 17MB + 코드 → 100MB 미만 | 여유 |
| CPU | 대부분 네트워크 대기 (호스트당 1초 간격) | 여유 |
| 실행 시간 | 정상 운영 시 하루 1~2분 | — |

첫 실행만 오래 걸린다. 곡목·출연진 보강이 1,600건을 훑기 때문에 30분쯤 잡으면 된다.
`--limit`으로 나눠서 며칠에 걸쳐 채워도 된다.

## 미리 확인할 것 두 가지

**1. OS 이미지 — Python 3.10 이상.**
코드가 `str | None`, `list[dict]` 같은 3.10 문법을 쓴다.
- Ubuntu 22.04 이상 → 기본 3.10 ✅
- Ubuntu 20.04(3.8), CentOS 7(3.6) → ❌. 이미지를 바꾸거나 pyenv로 따로 깔아야 한다.

**2. 아웃바운드 인터넷.**
NCP는 서버에 공인 IP가 없으면 바깥으로 못 나간다(VPC면 NAT Gateway 필요).
KOPIS·예당·롯데·슬랙에 전부 나가야 하므로 **공인 IP를 붙이거나 NAT를 둬야 한다.**
비용이 서버값보다 클 수도 있으니 콘솔에서 현재 요금을 확인할 것.
ACG(보안그룹)는 아웃바운드 TCP 80/443만 열려 있으면 된다. 인바운드는 SSH 외에 필요 없다.

## 설치

```bash
sudo apt update && sudo apt install -y python3-venv
sudo timedatectl set-timezone Asia/Seoul     # 중요. 아래 설명 참고

mkdir -p ~/concert-watch && cd ~/concert-watch
# 코드 복사 (git 또는 scp). concerts.db 도 같이 옮기면 좋다 - 아래 참고
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp .env.example .env && vi .env       # KOPIS_KEY / SLACK_BOT_TOKEN / SLACK_CHANNEL
chmod +x run_sync.sh
./run_sync.sh && tail -20 sync.log    # 첫 실행 확인
```

`truststore`는 설치되지 않는다(윈도우 전용 마커). 리눅스에는 필요 없고,
없으면 `net.py`가 알아서 건너뛴다.

## cron 등록

```bash
crontab -e
```
```cron
0 9 * * * /home/ubuntu/concert-watch/run_sync.sh
```

`run_sync.sh`가 작업 디렉터리와 `TZ`를 직접 잡는다. cron은 PATH도 cwd도
물려주지 않으므로 스크립트 안에서 처리해야 한다.

## 시간대가 중요한 이유

수집 구간을 `date.today()`로 잡고, 지난 공연 필터도 오늘 날짜를 쓴다.
서버가 UTC로 놀면 한국 시간 오전 9시가 UTC 0시라 **하루 어긋난다.**
`timedatectl set-timezone Asia/Seoul`을 하고, 그래도 `run_sync.sh`가
`TZ=Asia/Seoul`을 한 번 더 못박는다.

## DB를 옮길지 말지

- **옮긴다**: `notified` 기록이 따라와서 이미 보낸 공연을 다시 안 보낸다. 권장.
- **새로 만든다**: 첫 실행에 매칭 전부(현재 13건)가 한꺼번에 알림으로 간다.
  한 번 시끄럽고 마는 거라 이것도 괜찮다.

`concerts.db` 하나만 복사하면 된다. `.env`는 **절대 git에 올리지 말 것**
(`.gitignore`에 있다). scp로 따로 옮기거나 서버에서 직접 작성한다.

## 윈도우 PC와 병행하면

두 곳에서 같이 돌리면 **알림이 두 번 간다.** `notified`가 각자 따로 관리되기 때문.
서버로 옮겼으면 PC 쪽 작업을 지운다:

```powershell
schtasks /delete /tn "concert-watch" /f
```

## 잘 도는지 확인

```bash
tail -f sync.log
.venv/bin/python -m concert_watch matches
```
