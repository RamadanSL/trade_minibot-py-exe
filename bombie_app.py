import sys
import os
import json
import subprocess
import importlib.util

def ensure_package(pkg):
    if importlib.util.find_spec(pkg) is None:
        try:
            subprocess.check_call([sys.executable, '-m', 'pip', 'install', pkg])
        except Exception:
            pass
for pkg in ['requests', 'PySide6', 'pybit', 'numpy', 'pandas']:
    ensure_package(pkg)

import requests
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTextEdit, QFileDialog, QMessageBox, QScrollArea, QGroupBox, QComboBox)
from PySide6.QtCore import Qt
from pybit.unified_trading import HTTP
import numpy as np
import pandas as pd
import threading
import time
import tempfile

# --- Параметры по умолчанию ---
PARAMS = [
    ("API_KEY", "API ключ", ""),
    ("API_SECRET", "API секрет", ""),
    ("PROFIT_PERCENT", "Тейк-профит (%)", "0.02"),
    ("GRID_PERCENT", "Grid %", "0.01"),
    ("GRID_PART", "Grid доля", "0.2"),
    ("BEST_BUY_THRESHOLD", "Порог лучшей покупки", "0.01"),
    ("RSI_PERIOD", "RSI период", "14"),
    ("RSI_OVERBOUGHT", "RSI перекупленность", "70"),
    ("RSI_OVERSOLD", "RSI перепроданность", "30"),
    ("SMA_SHORT_PERIOD", "SMA короткая", "9"),
    ("SMA_LONG_PERIOD", "SMA длинная", "21"),
    ("TRAILING_STOP_ACTIVATION_PERCENT", "Активация трейлинг-стопа", "0.02"),
    ("TRAILING_STOP_PERCENT", "Трейлинг-стоп %", "0.01"),
    ("RISK_PER_TRADE_USDT", "Риск на сделку USDT", "5.0"),
    ("INTERVAL_PRICE", "Интервал цены (сек)", "10"),
    ("INTERVAL_TRADE", "Интервал торговли (сек)", "60")
]

DATA_DIR = os.path.join(os.path.dirname(sys.executable if getattr(sys, 'frozen', False) else __file__), "bombie_data")
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)
STATE_FILE = os.path.join(DATA_DIR, "trade_state.json")
LOG_FILE = os.path.join(DATA_DIR, "trade_log.txt")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")

# --- Работа с настройками ---
def ensure_state_files():
    if not os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump({}, f)
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            pass
    if not os.path.exists(SETTINGS_FILE):
        settings = {k: d for k, _, d in PARAMS}
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(settings, f, indent=2)

# --- Логирование ---
def log(message, level="INFO"):
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
    full_message = f"{timestamp} [{level}] {message}"
    try:
        with open(LOG_FILE, "a", encoding='utf-8') as f:
            f.write(full_message + "\n")
    except Exception:
        pass

# --- Торговая логика ---
class Trader:
    def __init__(self, settings):
        self.settings = settings
        self.session = None
        self.running = False
        self.thread = None
        self.symbol = "BOMBUSDT"
        self.asset_bomb = "BOMB"
        self.asset_usdt = "USDT"

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self.main_loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False

    def reload_settings(self, settings):
        self.settings = settings
        self.session = None

    def get_session(self):
        if self.session is None:
            self.session = HTTP(api_key=self.settings["API_KEY"], api_secret=self.settings["API_SECRET"])
        return self.session

    def get_balances(self):
        try:
            res = self.get_session().get_wallet_balance(accountType="UNIFIED")['result']['list'][0]
            usdt_balance = 0
            bomb_balance = 0
            for coin in res['coin']:
                if coin['coin'] == self.asset_usdt:
                    usdt_balance = float(coin['walletBalance'])
                elif coin['coin'] == self.asset_bomb:
                    bomb_balance = float(coin['walletBalance'])
            return {'bomb': bomb_balance, 'usdt': usdt_balance}
        except Exception as e:
            log(f"Ошибка получения баланса: {e}", level="ERROR")
            return {'bomb': 0, 'usdt': 0}

    def get_price(self):
        try:
            res = self.get_session().get_tickers(category="spot", symbol=self.symbol)
            return float(res['result']['list'][0]['lastPrice'])
        except Exception as e:
            log(f"Ошибка получения цены: {e}", level="ERROR")
            return None

    def get_lot_step(self):
        try:
            res = self.get_session().get_instruments_info(category="spot", symbol=self.symbol)
            return float(res['result']['list'][0]['lotSizeFilter']['basePrecision'])
        except Exception as e:
            log(f"Ошибка получения шага лота: {e}", level="ERROR")
            return 0.01

    def calculate_rsi(self, prices, period=14):
        if len(prices) < period: return None
        series = pd.Series(prices)
        delta = series.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi.iloc[-1]

    def calculate_sma(self, prices, period):
        if len(prices) < period: return None
        return pd.Series(prices).rolling(window=period).mean().iloc[-1]

    def get_market_data(self, limit=100):
        try:
            res = self.get_session().get_kline(category="spot", symbol=self.symbol, interval="5", limit=limit)
            candles = res['result']['list']
            if not candles: return None, None, None, None, None, None
            closes = [float(c[4]) for c in candles]
            min_price = min(closes)
            max_price = max(closes)
            avg_price = sum(closes) / len(closes)
            rsi = self.calculate_rsi(closes, int(self.settings["RSI_PERIOD"]))
            sma_short = self.calculate_sma(closes, int(self.settings["SMA_SHORT_PERIOD"]))
            sma_long = self.calculate_sma(closes, int(self.settings["SMA_LONG_PERIOD"]))
            return min_price, max_price, avg_price, rsi, sma_short, sma_long
        except Exception as e:
            log(f"Ошибка получения рыночных данных: {e}", level="ERROR")
            return None, None, None, None, None, None

    def round_step(self, value, step):
        if step == 0:
            return value
        return step * round(value / step)

    def buy(self, qty):
        try:
            lot_step = self.get_lot_step()
            qty = self.round_step(qty, lot_step)
            balances = self.get_balances()
            price = self.get_price()
            if qty <= 0 or price is None:
                log(f"Некорректное количество для покупки: {qty}", level="ERROR")
                return {'success': False, 'error': 'Некорректное количество'}
            cost = qty * price
            if cost > balances['usdt']:
                log(f"Недостаточно USDT для покупки: нужно {cost}, есть {balances['usdt']}", level="ERROR")
                return {'success': False, 'error': 'Недостаточно USDT'}
            order = self.get_session().place_order(category="spot", symbol=self.symbol, side="Buy", orderType="Market", qty=qty)
            log(f"УСПЕШНАЯ ПОКУПКА: {qty} BOMB по {price}. Ответ: {order}", level="TRADE")
            return {'success': True, 'order': order, 'qty': qty, 'price': price, 'usdt_left': balances['usdt'] - cost}
        except Exception as e:
            log(f"Ошибка покупки: {e}", level="ERROR")
            return {'success': False, 'error': str(e)}

    def sell(self, qty):
        try:
            lot_step = self.get_lot_step()
            qty = self.round_step(qty, lot_step)
            balances = self.get_balances()
            if qty <= 0:
                log(f"Некорректное количество для продажи: {qty}", level="ERROR")
                return {'success': False, 'error': 'Некорректное количество'}
            if qty > balances['bomb']:
                log(f"Недостаточно BOMB для продажи: нужно {qty}, есть {balances['bomb']}", level="ERROR")
                return {'success': False, 'error': 'Недостаточно BOMB'}
            price = self.get_price()
            order = self.get_session().place_order(category="spot", symbol=self.symbol, side="Sell", orderType="Market", qty=qty)
            log(f"УСПЕШНАЯ ПРОДАЖА: {qty} BOMB по {price}. Ответ: {order}", level="TRADE")
            return {'success': True, 'order': order, 'qty': qty, 'price': price, 'bomb_left': balances['bomb'] - qty}
        except Exception as e:
            log(f"Ошибка продажи: {e}", level="ERROR")
            return {'success': False, 'error': str(e)}

    def save_state(self, data):
        try:
            with tempfile.NamedTemporaryFile("w", delete=False, dir=".", encoding="utf-8") as tf:
                json.dump(data, tf, indent=4)
                tempname = tf.name
            os.replace(tempname, STATE_FILE)
        except Exception as e:
            log(f"Ошибка сохранения состояния: {e}", level="ERROR")

    def load_state(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                log(f"Ошибка загрузки состояния: {e}", level="ERROR")
                return {}
        return {}

    def main_loop(self):
        log("--- Скрипт запущен ---", level="START")
        last_trade_time = 0
        while self.running:
            try:
                price = self.get_price()
                if price is None:
                    time.sleep(10)
                    continue
                min6h, max6h, avg6h, rsi, sma_short, sma_long = self.get_market_data()
                now = time.time()
                if now - last_trade_time > int(self.settings["INTERVAL_TRADE"]):
                    state = self.load_state()
                    lot_step = self.get_lot_step()
                    balances = self.get_balances()
                    bomb_balance = float(balances['bomb'])
                    usdt_balance = float(balances['usdt'])
                    grid_center = state.get("grid_center", price)
                    last_buy_price = state.get("last_buy_price", 0)
                    peak_price_after_buy = state.get("peak_price_after_buy", 0)
                    if bomb_balance >= lot_step:
                        if price > peak_price_after_buy:
                            state["peak_price_after_buy"] = price
                            self.save_state(state)
                        trailing_stop_price = state["peak_price_after_buy"] * (1 - float(self.settings["TRAILING_STOP_PERCENT"]))
                        if last_buy_price > 0 and price > last_buy_price * (1 + float(self.settings["TRAILING_STOP_ACTIVATION_PERCENT"])) and price < trailing_stop_price and rsi is not None and rsi > int(self.settings["RSI_OVERSOLD"]):
                            log(f"ПЛАВАЮЩИЙ СТОП: Продажа {bomb_balance:.4f} BOMB по {price}", level="SELL")
                            self.sell(bomb_balance)
                            state.update({"last_buy_price": 0, "grid_center": price, "peak_price_after_buy": 0})
                            self.save_state(state)
                        elif last_buy_price > 0 and price > last_buy_price * (1 + float(self.settings["PROFIT_PERCENT"])) and rsi is not None and rsi > int(self.settings["RSI_OVERSOLD"]):
                            log(f"ТЕЙК-ПРОФИТ: Продажа {bomb_balance:.4f} BOMB по {price}", level="SELL")
                            self.sell(bomb_balance)
                            state.update({"last_buy_price": 0, "grid_center": price, "peak_price_after_buy": 0})
                            self.save_state(state)
                        elif price > grid_center * (1 + float(self.settings["GRID_PERCENT"])) and rsi is not None and rsi < int(self.settings["RSI_OVERBOUGHT"]) and sma_short and sma_long and sma_short > sma_long:
                            qty_to_sell = self.round_step(bomb_balance * float(self.settings["GRID_PART"]), lot_step)
                            if qty_to_sell >= lot_step:
                                log(f"GRID: Продажа части {qty_to_sell:.4f} BOMB по {price}", level="SELL")
                                self.sell(qty_to_sell)
                                state["grid_center"] = price
                                self.save_state(state)
                    elif usdt_balance > 1:
                        if bomb_balance < lot_step and min6h is not None and price <= min6h * (1 + float(self.settings["BEST_BUY_THRESHOLD"])) and rsi is not None and rsi < int(self.settings["RSI_OVERSOLD"]) and sma_short and sma_long and sma_short > sma_long:
                            stop_loss_price = price * (1 - float(self.settings["TRAILING_STOP_PERCENT"]))
                            qty_to_buy = float(self.settings["RISK_PER_TRADE_USDT"]) / (price - stop_loss_price) if price > stop_loss_price else 0
                            qty_to_buy = self.round_step(qty_to_buy, lot_step)
                            if qty_to_buy * price < usdt_balance and qty_to_buy > 0:
                                log(f"ЛУЧШАЯ ЦЕНА: Покупка {qty_to_buy:.4f} BOMB по {price}", level="BUY")
                                self.buy(qty_to_buy)
                                state.update({"last_buy_price": price, "grid_center": price, "peak_price_after_buy": price})
                                self.save_state(state)
                        elif price < grid_center * (1 - float(self.settings["GRID_PERCENT"])) and rsi is not None and rsi < int(self.settings["RSI_OVERSOLD"]) and sma_short and sma_long and sma_short > sma_long:
                            qty_to_buy = self.round_step((usdt_balance * float(self.settings["GRID_PART"])) / price, lot_step)
                            if qty_to_buy * price < usdt_balance and qty_to_buy > 0:
                                log(f"GRID: Покупка части {qty_to_buy:.4f} BOMB по {price}", level="BUY")
                                self.buy(qty_to_buy)
                                state["grid_center"] = price
                                self.save_state(state)
                    last_trade_time = now
                time.sleep(int(self.settings["INTERVAL_PRICE"]))
            except Exception as e:
                log(f"КРИТИЧЕСКАЯ ОШИБКА: {e}", level="FATAL")
                time.sleep(30)

GEMINI_API_KEY = "AIzaSyAhoTs2GnIUVI2BHZcNIc6k3GUDVUfsxrE"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"

TICKERS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "TONUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT",
    # Мемкоины
    "WIFUSDT", "PEPEUSDT", "FLOKIUSDT", "SHIBUSDT", "BONKUSDT", "TURBOUSDT", "MEMEUSDT", "POPCATUSDT", "BOOKOFMEMEUSDT",
    # Telegram games
    "BOMBIEUSDT", "TAPUSDT", "ZOOGAMEUSDT", "NOTPIXELUSDT", "MEMHASHUSDT", "SEEDUSDT", "PAWSUSDT", "POCKETFIUSDT", "ICEBERGUSDT", "BCOIN2048USDT",
    # Новые игровые/мемные токены
    "HAMSTERUSDT", "MOGUSDT", "BRETTUSDT", "POPCATUSDT", "BABYDOGEUSDT", "BLASTUSDT", "CHILLGUYUSDT", "OLUSDT", "MEMEFIUSDT", "MORPHOUSDT", "PNUTUSDT", "ZRCUSDT"
]

def get_ai_signal(ticker):
    prompt = f"Дай краткий торговый совет (buy/sell/hold) по {ticker} на 1 час. Только одно слово."
    data = {
        "contents": [
            {"parts": [{"text": prompt}]}
        ]
    }
    try:
        resp = requests.post(GEMINI_URL, json=data, headers={"Content-Type": "application/json"}, timeout=10)
        if resp.ok:
            txt = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip().lower()
            if "buy" in txt:
                return "buy"
            if "sell" in txt:
                return "sell"
            if "hold" in txt:
                return "hold"
            return txt
        return "no signal"
    except Exception as e:
        return f"err: {e}"

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Bombie Bybit Bot")
        self.env_vars = {}
        self.settings = self.load_settings()
        self.is_trading = False
        self.selected_ticker = TICKERS[0]
        self.init_ui()

    def load_settings(self):
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {k: d for k, _, d in PARAMS}
        return {k: d for k, _, d in PARAMS}

    def save_settings(self):
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump({k: self.env_vars[k].text() for k, _, _ in PARAMS}, f, ensure_ascii=False, indent=2)

    def init_ui(self):
        self.setStyleSheet('''
            QMainWindow { background: #23272e; }
            QLabel { color: #e0e0e0; font-size: 15px; }
            QGroupBox { border: 2px solid #444; border-radius: 8px; margin-top: 12px; background: #282c34; }
            QGroupBox:title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #ffb300; font-weight: bold; font-size: 16px; }
            QLineEdit { background: #181a20; color: #fff; border: 1px solid #555; border-radius: 5px; padding: 4px; font-size: 15px; }
            QPushButton { background: #ffb300; color: #23272e; border-radius: 6px; padding: 7px 18px; font-weight: bold; font-size: 15px; }
            QPushButton:hover { background: #ffd54f; }
            QComboBox { background: #181a20; color: #fff; border: 1px solid #555; border-radius: 5px; padding: 4px; font-size: 15px; }
        ''')
        central = QWidget()
        vbox = QVBoxLayout()
        title = QLabel("Bombie Bybit Bot")
        title.setStyleSheet("font-size: 26px; font-weight: bold; color: #ffb300; margin-bottom: 18px;")
        title.setAlignment(Qt.AlignCenter)
        vbox.addWidget(title)
        # --- Описание программы ---
        desc = QLabel("""
<b>Bombie — автономный торговый бот для Bybit</b><br>
• Поддержка AI-сигналов Gemini (Google)
• Выбор любого тикера: топовые, мемкоины, игровые токены (Telegram)
• Portable-режим: все файлы только в подпапке, ничего лишнего
• Управление и настройка через красивый GUI (PySide6)
• Кнопки старт/стоп торговли, сохранение параметров
        """)
        desc.setStyleSheet("color: #b0b0b0; font-size: 14px; margin-bottom: 10px;")
        desc.setWordWrap(True)
        vbox.addWidget(desc)
        group = QGroupBox("Параметры")
        form = QVBoxLayout()
        for key, label, _ in PARAMS:
            row = QHBoxLayout()
            row.addWidget(QLabel(f"{label} ({key}):"))
            edit = QLineEdit(self.settings.get(key, ""))
            self.env_vars[key] = edit
            row.addWidget(edit)
            form.addLayout(row)
        group.setLayout(form)
        vbox.addWidget(group)
        # --- Выбор тикера ---
        ticker_row = QHBoxLayout()
        ticker_row.addWidget(QLabel("Тикер:"))
        self.ticker_combo = QComboBox()
        self.ticker_combo.addItems(TICKERS)
        self.ticker_combo.currentTextChanged.connect(self.on_ticker_change)
        ticker_row.addWidget(self.ticker_combo)
        vbox.addLayout(ticker_row)
        # --- AI сигнал ---
        self.ai_signal_label = QLabel("AI сигнал: ...")
        vbox.addWidget(self.ai_signal_label)
        btn_ai = QPushButton("Получить AI сигнал")
        btn_ai.clicked.connect(self.update_ai_signal)
        vbox.addWidget(btn_ai)
        # --- Кнопки управления ---
        btns = QHBoxLayout()
        self.btn_start = QPushButton("Старт торговли")
        self.btn_start.clicked.connect(self.start_trading)
        btns.addWidget(self.btn_start)
        self.btn_stop = QPushButton("Стоп торговли")
        self.btn_stop.clicked.connect(self.stop_trading)
        btns.addWidget(self.btn_stop)
        vbox.addLayout(btns)
        btn_save = QPushButton("Сохранить настройки")
        btn_save.clicked.connect(self.save_settings)
        vbox.addWidget(btn_save)
        vbox.addStretch(1)
        central.setLayout(vbox)
        self.setCentralWidget(central)
        self.update_ai_signal()

    def on_ticker_change(self, ticker):
        self.selected_ticker = ticker
        self.update_ai_signal()

    def update_ai_signal(self):
        sig = get_ai_signal(self.selected_ticker)
        self.ai_signal_label.setText(f"AI сигнал: {sig}")

    def start_trading(self):
        self.is_trading = True
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        # ... здесь запуск торгового цикла ...

    def stop_trading(self):
        self.is_trading = False
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        # ... здесь остановка торгового цикла ...

if __name__ == "__main__":
    app = QApplication(sys.argv)
    mw = MainWindow()
    mw.show()
    sys.exit(app.exec()) 