@echo off
REM concert-watch 일일 동기화. 작업 스케줄러가 이 파일을 실행한다.
REM 여기서 작업 디렉터리를 잡아야 .env / watchlist.yml / concerts.db 를 찾는다.
cd /d "D:\workspace\my-project-2"
"C:\Python314\python.exe" -m concert_watch sync > sync.log 2>&1
exit /b %ERRORLEVEL%
