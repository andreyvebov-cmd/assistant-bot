@echo off
cd /d J:\impres\app\strategy-bot
set PYTHONIOENCODING=utf-8
REM Локальный поисковый мост: прокси + туннель стартуют вместе с ботом
start "" C:\Python314\pythonw.exe -u search_proxy.py
start "" C:\Python314\pythonw.exe -u search_bridge.py
start "" C:\Python314\pythonw.exe -u bot.py > bot.log 2>&1
exit
