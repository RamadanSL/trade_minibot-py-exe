@echo off
REM --- Автоматическая выгрузка проекта на GitHub ---
REM Требуется установленный git и авторизация (ssh или https)

set REPO=https://github.com/RamadanSL/trade_minibot-py-exe.git
set MSG=auto upload

if not exist .git (
    git init
    git remote add origin %REPO%
) else (
    git remote set-url origin %REPO%
)

git add .
git commit -m "%MSG%"
git branch -M main
git push -f origin main

pause 