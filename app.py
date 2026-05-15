import math
import os
import threading
import webbrowser
import pandas as pd
from functools import wraps
from flask import Flask, jsonify, render_template, request, session, redirect, url_for
import yfinance as yf

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
LOCAL_URL = "http://127.0.0.1:5000"

APP_PASSWORD = os.environ.get("APP_PASSWORD", "88888888")
SECRET_KEY   = os.environ.get("SECRET_KEY",   "stock-tool-x9k2p7q4m1")

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["PERMANENT_SESSION_LIFETIME"] = 60 * 60 * 24 * 30  # 30 天


# ── 登入驗證 ─────────────────────────────────────────────────────────────────
@app.before_request
def require_login():
    allowed = {"login", "static"}
    if request.endpoint in allowed:
        return
    if not session.get("authenticated"):
        if request.path.startswith("/api/"):
            return jsonify({"error": "請先登入"}), 401
        return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if request.form.get("password") == APP_PASSWORD:
            session.permanent = True
            session["authenticated"] = True
            return redirect(url_for("index"))
        error = "密碼錯誤，請再試一次"
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


def safe_round(val, digits=2):
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return round(f, digits)
    except (TypeError, ValueError):
        return None


def safe_int(val):
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return int(f)
    except (TypeError, ValueError):
        return None


def get_company_name(ticker, symbol):
    """Try fast_info first, then info.longName as fallback."""
    try:
        name = getattr(ticker.fast_info, "company_name", None)
        if name and name != "N/A" and name != symbol:
            return name
    except Exception:
        pass
    try:
        info = ticker.info
        return info.get("longName") or info.get("shortName") or symbol
    except Exception:
        return symbol


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/quote")
def get_quote():
    symbol = request.args.get("symbol", "").strip()
    if not symbol:
        return jsonify({"error": "請輸入股票代號"}), 400

    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="5d", interval="1d")
        if hist.empty:
            return jsonify({"error": f"找不到「{symbol}」的資料，請確認代號格式（台股請加 .TW，港股請加 .HK）"}), 404

        # Drop rows where Close is NaN (未結算或休市)
        valid = hist.dropna(subset=["Close"])
        if valid.empty:
            return jsonify({"error": f"「{symbol}」目前無有效收盤價資料"}), 404

        latest = valid.iloc[-1]
        prev = valid.iloc[-2] if len(valid) > 1 else valid.iloc[-1]

        close = float(latest["Close"])
        prev_close = float(prev["Close"])
        change = close - prev_close
        change_pct = (change / prev_close * 100) if prev_close != 0 else 0

        currency = ""
        year_high = None
        year_low = None
        mkt_cap = None

        try:
            fi = ticker.fast_info
            currency = getattr(fi, "currency", "") or ""
            year_high = getattr(fi, "year_high", None)
            year_low = getattr(fi, "year_low", None)
            mkt_cap = getattr(fi, "market_cap", None)
        except Exception:
            pass

        name = get_company_name(ticker, symbol)

        return jsonify({
            "symbol": symbol.upper(),
            "name": name,
            "currency": currency,
            "price": safe_round(close),
            "open": safe_round(float(latest["Open"])),
            "high": safe_round(float(latest["High"])),
            "low": safe_round(float(latest["Low"])),
            "close": safe_round(close),
            "volume": safe_int(latest["Volume"]),
            "change": safe_round(change),
            "change_pct": safe_round(change_pct),
            "prev_close": safe_round(prev_close),
            "date": latest.name.strftime("%Y-%m-%d"),
            "year_high": safe_round(year_high),
            "year_low": safe_round(year_low),
            "market_cap": safe_int(mkt_cap),
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/history")
def get_history():
    symbol = request.args.get("symbol", "").strip()
    period = request.args.get("period", "3mo")
    interval = request.args.get("interval", "1d")

    if not symbol:
        return jsonify({"error": "請輸入股票代號"}), 400

    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period=period, interval=interval)
        if hist.empty:
            return jsonify({"error": f"找不到「{symbol}」的歷史資料"}), 404

        valid = hist.dropna(subset=["Close"])
        data = [
            {
                "time": idx.strftime("%Y-%m-%d"),
                "open": safe_round(float(row["Open"]), 4),
                "high": safe_round(float(row["High"]), 4),
                "low": safe_round(float(row["Low"]), 4),
                "close": safe_round(float(row["Close"]), 4),
                "volume": safe_int(row["Volume"]),
            }
            for idx, row in valid.iterrows()
        ]

        return jsonify(data)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/score")
def get_score():
    symbol = request.args.get("symbol", "").strip()
    if not symbol:
        return jsonify({"error": "請輸入股票代號"}), 400
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="3mo", interval="1d")
        if hist.empty:
            return jsonify({"error": f"找不到「{symbol}」的資料"}), 404
        df = hist.dropna(subset=["Close"]).copy()
        if len(df) < 14:
            return jsonify({"error": "資料不足（至少需要14個交易日）"}), 400

        close  = df["Close"]
        high   = df["High"]
        low    = df["Low"]
        volume = df["Volume"]
        cur    = float(close.iloc[-1])

        # ── 1. 趨勢方向 (0-30) ───────────────────────────────
        ma5  = float(close.rolling(5).mean().iloc[-1])
        ma10 = float(close.rolling(10).mean().iloc[-1])
        ma20 = float(close.rolling(20).mean().iloc[-1])
        ma60 = float(close.rolling(60).mean().iloc[-1]) if len(close) >= 60 else None

        trend = 0
        if cur > ma20:              trend += 12
        if ma5  > ma20:             trend += 10
        if cur > ma10:              trend +=  5
        if ma60 and cur > ma60:     trend +=  8
        if len(close) >= 5:
            mom5 = (cur / float(close.iloc[-5]) - 1) * 100
            if mom5 > 3:   trend = min(30, trend + 5)
            elif mom5 < -3: trend = max(0,  trend - 5)
        trend = min(30, trend)

        # ── 2. RSI 動能 (0-25) ────────────────────────────────
        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rs    = gain / loss.replace(0, float("nan"))
        rsi   = float((100 - 100 / (1 + rs)).iloc[-1])

        if math.isnan(rsi):                   rsi_s = 12
        elif 50 <= rsi <= 70:                 rsi_s = 25
        elif 40 <= rsi < 50:                  rsi_s = 16
        elif 70 < rsi <= 80:                  rsi_s = 14
        elif rsi > 80:                        rsi_s = 8
        elif 30 <= rsi < 40:                  rsi_s = 10
        else:                                 rsi_s = 5

        # ── 3. MACD 信號 (0-25) ───────────────────────────────
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd  = ema12 - ema26
        sig   = macd.ewm(span=9, adjust=False).mean()
        histo = macd - sig

        macd_v = float(macd.iloc[-1])
        hist_v = float(histo.iloc[-1])
        hist_p = float(histo.iloc[-2]) if len(histo) >= 2 else 0

        macd_s = 0
        if macd_v > 0:                        macd_s += 8
        if macd_v > float(sig.iloc[-1]):      macd_s += 10
        if hist_v > 0 and hist_v > hist_p:    macd_s += 7
        elif hist_v > 0:                      macd_s += 4
        macd_s = min(25, macd_s)

        # ── 4. 量價配合 (0-20) ────────────────────────────────
        avg_vol = float(volume.rolling(20).mean().iloc[-1]) if len(volume) >= 20 else float(volume.mean())
        vol_ratio = float(volume.iloc[-1]) / avg_vol if avg_vol > 0 else 1.0
        price_up  = cur > float(close.iloc[-2])

        if   vol_ratio > 1.5 and price_up:   vol_s = 20
        elif vol_ratio > 1.0 and price_up:   vol_s = 14
        elif vol_ratio > 0.8 and price_up:   vol_s = 10
        elif vol_ratio > 1.0 and not price_up: vol_s = 5
        else:                                vol_s = 8

        total = trend + rsi_s + macd_s + vol_s

        # ── 信號 ──────────────────────────────────────────────
        if   total >= 80: signal, sig_text = "strong_buy", "強力買進"
        elif total >= 65: signal, sig_text = "buy",        "買進"
        elif total >= 45: signal, sig_text = "hold",       "持有"
        elif total >= 30: signal, sig_text = "watch",      "觀望"
        else:             signal, sig_text = "sell",       "賣出"

        # ── 風險（年化波動率）─────────────────────────────────
        ann_vol = float(close.pct_change().dropna().rolling(20).std().iloc[-1]) * math.sqrt(252) * 100
        if   ann_vol < 20: risk_lv, risk_k = "低風險", "low"
        elif ann_vol < 40: risk_lv, risk_k = "中風險", "mid"
        else:              risk_lv, risk_k = "高風險", "high"

        # ── 止損（ATR × 2）────────────────────────────────────
        tr  = pd.concat([high - low,
                         (high - close.shift(1)).abs(),
                         (low  - close.shift(1)).abs()], axis=1).max(axis=1)
        atr = float(tr.rolling(14).mean().iloc[-1])
        stop_loss = round(cur - 2 * atr, 2)

        # ── 簡析文字 ──────────────────────────────────────────
        tips = []
        if trend >= 22:    tips.append("均線多頭排列，趨勢向上")
        elif trend <= 8:   tips.append("價格跌破均線，趨勢偏弱")
        else:              tips.append("趨勢方向中性，留意突破方向")

        if not math.isnan(rsi):
            if rsi >= 70:  tips.append(f"RSI {rsi:.0f}，接近超買區，注意拉回風險")
            elif rsi >= 50: tips.append(f"RSI {rsi:.0f}，動能處多頭健康區間")
            elif rsi >= 30: tips.append(f"RSI {rsi:.0f}，動能偏弱，留意止跌訊號")
            else:           tips.append(f"RSI {rsi:.0f}，深度超賣，可留意反彈")

        if   macd_s >= 20: tips.append("MACD 金叉且柱體擴張，動能強勁")
        elif macd_s <= 8:  tips.append("MACD 死叉或動能衰退，謹慎操作")

        if   vol_s >= 15:  tips.append("量增價漲，籌碼積極進場")
        elif vol_s <= 5:   tips.append("量增但價跌，注意賣壓出籠")

        return jsonify({
            "score":      total,
            "signal":     signal,
            "signal_text": sig_text,
            "components": {
                "trend":  {"score": trend,  "max": 30, "label": "趨勢方向"},
                "rsi":    {"score": rsi_s,  "max": 25, "label": "RSI 動能"},
                "macd":   {"score": macd_s, "max": 25, "label": "MACD 信號"},
                "volume": {"score": vol_s,  "max": 20, "label": "量價配合"},
            },
            "rsi_value":  safe_round(rsi),
            "volatility": safe_round(ann_vol),
            "risk_level": risk_lv,
            "risk_key":   risk_k,
            "stop_loss":  safe_round(stop_loss),
            "ma5":        safe_round(ma5),
            "ma20":       safe_round(ma20),
            "tips":       tips[:3],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/chart")
def get_chart():
    symbol = request.args.get("symbol", "").strip()
    period = request.args.get("period", "3mo")
    interval = request.args.get("interval", "1d")
    if not symbol:
        return jsonify({"error": "請輸入股票代號"}), 400
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period=period, interval=interval)
        if hist.empty:
            return jsonify({"error": f"找不到「{symbol}」的歷史資料"}), 404
        df = hist.dropna(subset=["Close"]).copy()
        if df.empty:
            return jsonify({"error": "無有效歷史資料"}), 404

        times = [idx.strftime("%Y-%m-%d") for idx in df.index]
        close = df["Close"]
        high  = df["High"]
        low   = df["Low"]
        open_ = df["Open"]
        vol   = df["Volume"]

        def line(s):
            out = []
            for t, v in zip(times, s):
                try:
                    f = float(v)
                    if math.isnan(f) or math.isinf(f):
                        out.append({"time": t})          # whitespace: keeps time axis aligned
                    else:
                        out.append({"time": t, "value": round(f, 4)})
                except Exception:
                    out.append({"time": t})
            return out

        candles, volume_bars = [], []
        for t, o, h, l, c, v in zip(times, open_, high, low, close, vol):
            o, h, l, c = safe_round(o, 4), safe_round(h, 4), safe_round(l, 4), safe_round(c, 4)
            if None in (o, h, l, c):
                continue
            candles.append({"time": t, "open": o, "high": h, "low": l, "close": c})
            volume_bars.append({"time": t, "value": safe_int(v) or 0,
                                "color": "#f8514955" if c >= o else "#3fb95055"})

        ma = {str(p): line(close.rolling(p).mean()) for p in [5, 10, 20, 60]}

        ma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        bb = {"upper": line(ma20 + 2*std20), "mid": line(ma20), "lower": line(ma20 - 2*std20)}

        d = close.diff()
        gain = d.clip(lower=0).rolling(14).mean()
        loss = (-d.clip(upper=0)).rolling(14).mean()
        rsi_vals = line(100 - 100 / (1 + gain / loss.replace(0, float("nan"))))

        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        sig_line  = macd_line.ewm(span=9, adjust=False).mean()
        hist_line = macd_line - sig_line
        macd_hist = []
        for t, v in zip(times, hist_line):
            sv = safe_round(v, 4)
            if sv is not None:
                macd_hist.append({"time": t, "value": sv, "color": "#f85149" if sv >= 0 else "#3fb950"})
            else:
                macd_hist.append({"time": t})
        macd = {"line": line(macd_line), "signal": line(sig_line), "hist": macd_hist}

        low9  = low.rolling(9).min()
        high9 = high.rolling(9).max()
        rsv = (close - low9) / (high9 - low9).replace(0, float("nan")) * 100
        k = rsv.ewm(com=2, adjust=False).mean()
        kd = {"k": line(k), "d": line(k.ewm(com=2, adjust=False).mean())}

        return jsonify({"candles": candles, "volume": volume_bars,
                        "ma": ma, "bb": bb, "rsi": rsi_vals, "macd": macd, "kd": kd})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/scan")
def get_scan():
    symbols_raw  = request.args.get("symbols", "").strip()
    strategy     = request.args.get("strategy", "momentum")

    # ── Strategy-specific tunable parameters ────────────────────
    # momentum
    p_min_strong   = int(float(request.args.get("min_strong",   70)))
    p_min_mid      = int(float(request.args.get("min_mid",      60)))
    # ma_cross
    p_ma_short     = int(float(request.args.get("ma_short",      5)))
    p_ma_mid       = int(float(request.args.get("ma_mid",       10)))
    p_ma_long      = int(float(request.args.get("ma_long",      20)))
    p_cross_days   = int(float(request.args.get("cross_days",    5)))
    # breakout
    p_break_period = int(float(request.args.get("break_period", 20)))
    p_vol_mult     = float(request.args.get("vol_mult",         1.2))
    p_near_pct     = float(request.args.get("near_pct",        0.98))
    # reversal
    p_rsi_strong   = float(request.args.get("rsi_strong",       35))
    p_rsi_mild     = float(request.args.get("rsi_mild",         40))
    p_rsi_moderate = float(request.args.get("rsi_moderate",     45))

    if not symbols_raw:
        return jsonify({"error": "請輸入股票代號"}), 400

    symbols = list(dict.fromkeys(
        s.strip().upper() for s in symbols_raw.split(",") if s.strip()
    ))[:20]
    if not symbols:
        return jsonify({"error": "請輸入有效的股票代號"}), 400

    results, failed = [], []

    for symbol in symbols:
        try:
            ticker = yf.Ticker(symbol)
            hist   = ticker.history(period="3mo", interval="1d")
            if hist.empty:
                failed.append(symbol); continue
            df = hist.dropna(subset=["Close"]).copy()
            if len(df) < 14:
                failed.append(symbol); continue

            close  = df["Close"]
            high   = df["High"]
            volume = df["Volume"]
            cur    = float(close.iloc[-1])
            prev   = float(close.iloc[-2])

            # ── Score (same weights as /api/score) ──────────
            ma5  = float(close.rolling(5).mean().iloc[-1])
            ma10 = float(close.rolling(10).mean().iloc[-1])
            ma20 = float(close.rolling(20).mean().iloc[-1])
            ma60 = float(close.rolling(60).mean().iloc[-1]) if len(close) >= 60 else None

            trend = 0
            if cur > ma20:           trend += 12
            if ma5  > ma20:          trend += 10
            if cur > ma10:           trend +=  5
            if ma60 and cur > ma60:  trend +=  8
            if len(close) >= 5:
                mom = (cur / float(close.iloc[-5]) - 1) * 100
                if mom > 3:   trend = min(30, trend + 5)
                elif mom < -3: trend = max(0, trend - 5)
            trend = min(30, trend)

            delta = close.diff()
            gain  = delta.clip(lower=0).rolling(14).mean()
            loss  = (-delta.clip(upper=0)).rolling(14).mean()
            rsi_s = (100 - 100 / (1 + gain / loss.replace(0, float("nan"))))
            rsi_v = float(rsi_s.iloc[-1])
            if math.isnan(rsi_v):       rs = 12
            elif 50 <= rsi_v <= 70:     rs = 25
            elif 40 <= rsi_v < 50:      rs = 16
            elif 70 < rsi_v <= 80:      rs = 14
            elif rsi_v > 80:            rs = 8
            elif 30 <= rsi_v < 40:      rs = 10
            else:                       rs = 5

            ema12 = close.ewm(span=12, adjust=False).mean()
            ema26 = close.ewm(span=26, adjust=False).mean()
            macd  = ema12 - ema26
            sig   = macd.ewm(span=9, adjust=False).mean()
            histo = macd - sig
            mv    = float(macd.iloc[-1])
            hv    = float(histo.iloc[-1])
            hp    = float(histo.iloc[-2]) if len(histo) >= 2 else 0
            ms = 0
            if mv > 0:                   ms += 8
            if mv > float(sig.iloc[-1]): ms += 10
            if hv > 0 and hv > hp:       ms += 7
            elif hv > 0:                 ms += 4
            ms = min(25, ms)

            avg_vol   = float(volume.rolling(20).mean().iloc[-1]) if len(volume) >= 20 else float(volume.mean())
            vol_ratio = float(volume.iloc[-1]) / avg_vol if avg_vol > 0 else 1.0
            price_up  = cur > prev
            if   vol_ratio > 1.5 and price_up:     vs = 20
            elif vol_ratio > 1.0 and price_up:     vs = 14
            elif vol_ratio > 0.8 and price_up:     vs = 10
            elif vol_ratio > 1.0 and not price_up: vs = 5
            else:                                  vs = 8

            total = trend + rs + ms + vs
            chg   = (cur - prev) / prev * 100 if prev != 0 else 0

            if   total >= 80: sig_txt = "強力買進"
            elif total >= 65: sig_txt = "買進"
            elif total >= 45: sig_txt = "持有"
            elif total >= 30: sig_txt = "觀望"
            else:             sig_txt = "賣出"

            # ── Strategy check (uses tunable params) ─────────────
            ss, sm = 50, False

            if strategy == "ma_cross":
                s_ma  = close.rolling(p_ma_short).mean()
                m_ma  = close.rolling(p_ma_mid).mean()
                l_ma  = close.rolling(p_ma_long).mean()
                cur_s = float(s_ma.iloc[-1])
                cur_m = float(m_ma.iloc[-1])
                cur_l = float(l_ma.iloc[-1])
                crossed = bool(((s_ma > l_ma) & (s_ma.shift(1) <= l_ma.shift(1))).iloc[-p_cross_days:].any())
                if crossed:                  ss, sm = 100, True
                elif cur_s > cur_m > cur_l:  ss, sm = 80,  True
                elif cur_s > cur_l:          ss, sm = 50,  False
                else:                        ss, sm = 20,  False

            elif strategy == "breakout":
                lookback = max(p_break_period, 5)
                h_ref = float(high.rolling(lookback).max().iloc[-2])
                if cur > h_ref:
                    ss = 100 if vol_ratio > p_vol_mult else 75
                    sm = True
                elif cur > h_ref * p_near_pct:
                    ss, sm = 60, True
                else:
                    ss = max(0, int(50 + (cur / h_ref - 1) * 1000))
                    sm = False

            elif strategy == "reversal":
                rsi_prev = float(rsi_s.iloc[-2]) if len(rsi_s) >= 2 else rsi_v
                rising   = rsi_v > rsi_prev
                if rsi_v < p_rsi_strong and rising:    ss, sm = 100, True
                elif rsi_v < p_rsi_mild and rising:    ss, sm = 80,  True
                elif rsi_v < p_rsi_moderate and rising: ss, sm = 60, True
                else:                                  ss, sm = 20,  False

            else:  # momentum
                if total >= p_min_strong:   ss, sm = 100, True
                elif total >= p_min_mid:    ss, sm = 75,  True
                elif total >= 50:           ss, sm = 50,  False
                else:                       ss, sm = total, False

            # ── Name (fast only) ─────────────────────────────
            try:
                name = getattr(ticker.fast_info, "company_name", None) or symbol
                if not name or name == "N/A": name = symbol
            except Exception:
                name = symbol

            results.append({
                "symbol":      symbol,
                "name":        name,
                "price":       safe_round(cur),
                "change_pct":  safe_round(chg),
                "score":       total,
                "signal":      sig_txt,
                "rsi":         safe_round(rsi_v),
                "strat_score": ss,
                "strat_match": sm,
            })
        except Exception:
            failed.append(symbol)

    results.sort(key=lambda x: (x["strat_match"], x["strat_score"]), reverse=True)
    return jsonify({
        "results": results,
        "total":   len(symbols),
        "matched": sum(1 for r in results if r["strat_match"]),
        "failed":  failed,
        "strategy": strategy,
    })


# ─── Market Sentiment Dashboard ───────────────────────────────────────────────
_market_cache: dict = {"data": None, "ts": 0.0}

_MARKET_SYMS = {
    "tw_idx": ("^TWII",   "台灣加權"),
    "tw_50":  ("0050.TW", "台灣50"),
    "sp500":  ("^GSPC",   "S&P 500"),
    "nasdaq": ("^IXIC",   "NASDAQ"),
    "dji":    ("^DJI",    "道瓊"),
    "vix":    ("^VIX",    "VIX 恐慌"),
    "gold":   ("GC=F",    "黃金"),
    "tnx":    ("^TNX",    "美10Y債"),
}


def _fetch_market_sym(args):
    key, sym, name = args
    try:
        hist  = yf.Ticker(sym).history(period="1mo", interval="1d").dropna(subset=["Close"])
        close = hist["Close"]
        if len(close) < 2:
            return key, {"name": name, "symbol": sym, "error": True}
        cur, prev = float(close.iloc[-1]), float(close.iloc[-2])
        chg = (cur - prev) / prev * 100 if prev else 0
        rsi = None
        if len(close) >= 15:
            d     = close.diff()
            gain  = d.clip(lower=0).rolling(14).mean()
            loss  = (-d.clip(upper=0)).rolling(14).mean()
            ll    = float(loss.iloc[-1])
            if ll:
                rsi = float(100 - 100 / (1 + gain.iloc[-1] / ll))
        mom = None
        if len(close) >= 20:
            ma20 = float(close.rolling(20).mean().iloc[-1])
            if ma20:
                mom = (cur - ma20) / ma20 * 100
        return key, {
            "name": name, "symbol": sym,
            "price":      safe_round(cur, 2),
            "change_pct": safe_round(chg, 2),
            "rsi":        safe_round(rsi, 1),
            "momentum":   safe_round(mom, 2),
        }
    except Exception:
        return key, {"name": name, "symbol": sym, "error": True}


def _compute_sentiment(d):
    score, factors = 0, []

    vix = d.get("vix", {})
    if not vix.get("error") and vix.get("price") is not None:
        v  = vix["price"]
        vs = 30 if v < 12 else 25 if v < 16 else 18 if v < 20 else 10 if v < 25 else 5 if v < 30 else 0
        score += vs
        factors.append({"label": "恐慌指數 VIX", "score": vs, "max": 30,
                         "val": f"{v:.1f}", "note": "越低越貪婪"})

    sp = d.get("sp500", {})
    if not sp.get("error") and sp.get("rsi") is not None:
        r  = sp["rsi"]
        rs = 25 if 50 <= r <= 70 else 20 if r > 70 else 15 if 40 <= r < 50 else 8 if 30 <= r < 40 else 3
        score += rs
        factors.append({"label": "S&P 500 RSI", "score": rs, "max": 25,
                         "val": f"{r:.1f}", "note": "50–70 最佳"})

    if not sp.get("error") and sp.get("momentum") is not None:
        m  = sp["momentum"]
        ms = 25 if m > 5 else 20 if m > 2 else 14 if m > 0 else 8 if m > -2 else 3
        score += ms
        factors.append({"label": "S&P 500 動能", "score": ms, "max": 25,
                         "val": f"{m:+.1f}%", "note": "相對20日均線"})

    tw = d.get("tw_idx", {})
    if not tw.get("error") and tw.get("rsi") is not None:
        r  = tw["rsi"]
        ts = 20 if 50 <= r <= 70 else 16 if r > 70 else 12 if 40 <= r < 50 else 6 if 30 <= r < 40 else 2
        score += ts
        factors.append({"label": "台股加權 RSI", "score": ts, "max": 20,
                         "val": f"{r:.1f}", "note": "50–70 最佳"})

    total = min(100, max(0, score))
    if   total >= 75: label, key = "極度貪婪", "extreme_greed"
    elif total >= 60: label, key = "貪婪",     "greed"
    elif total >= 45: label, key = "中立",     "neutral"
    elif total >= 30: label, key = "恐慌",     "fear"
    else:             label, key = "極度恐慌", "extreme_fear"
    return {"score": total, "label": label, "key": key, "factors": factors}


@app.route("/api/market")
def get_market():
    import time as _t
    from concurrent.futures import ThreadPoolExecutor
    force = request.args.get("force", "0") == "1"
    now   = _t.time()
    if not force and _market_cache["data"] and now - _market_cache["ts"] < 300:
        return jsonify(_market_cache["data"])

    tasks = [(k, sym, name) for k, (sym, name) in _MARKET_SYMS.items()]
    with ThreadPoolExecutor(max_workers=8) as ex:
        market = dict(ex.map(_fetch_market_sym, tasks))

    market["sentiment"] = _compute_sentiment(market)
    market["updated"]   = __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M")
    _market_cache["data"] = market
    _market_cache["ts"]   = now
    return jsonify(market)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    is_local = os.environ.get("RENDER") is None and os.environ.get("RAILWAY_ENVIRONMENT") is None
    if is_local:
        print(f"啟動中... 自動以 Chrome 開啟 {LOCAL_URL}")
        webbrowser.register("chrome", None, webbrowser.BackgroundBrowser(CHROME))
        threading.Timer(1.5, lambda: webbrowser.get("chrome").open(LOCAL_URL)).start()
        app.run(debug=False, port=port, host="127.0.0.1")
    else:
        app.run(debug=False, port=port, host="0.0.0.0")
