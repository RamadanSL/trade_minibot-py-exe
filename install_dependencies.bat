@echo off
echo Обновление зависимостей...
py -m pip install --upgrade pyinstaller PySide6 requests pybit numpy pandas
echo Все зависимости обновлены!
pause 