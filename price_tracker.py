import logging
import sqlite3
import asyncio
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Any, Tuple

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 네트워크 ID 매핑
NETWORK_MAPPING = {
    "ethereum": "eth",
    "bsc": "bsc",
    "polygon": "polygon_pos",
    "arbitrum": "arbitrum",
    "avalanche": "avax",
    "optimism": "optimism",
    "base": "base",
    "solana": "solana"
}

# OHLC 데이터베이스 초기화
def init_ohlc_db():
    """
    OHLC 데이터를 저장할 데이터베이스 테이블을 초기화합니다.
    """
    conn = sqlite3.connect('tokens.db')
    cursor = conn.cursor()
    
    # OHLC 데이터 테이블 생성
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS token_ohlc (
        token_address TEXT,
        network TEXT,
        timestamp TEXT,
        open REAL,
        high REAL,
        low REAL,
        close REAL,
        volume REAL DEFAULT 0,
        interval TEXT,
        PRIMARY KEY (token_address, network, timestamp, interval)
    )
    ''')
    
    # 사용자별 OHLC 알림 설정 테이블
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS ohlc_alerts (
        user_id INTEGER,
        token_address TEXT,
        network TEXT,
        alert_type TEXT,
        threshold REAL,
        enabled INTEGER DEFAULT 1,
        last_alert TIMESTAMP,
        PRIMARY KEY (user_id, token_address, network, alert_type)
    )
    ''')
    
    conn.commit()
    conn.close()
    logger.info("OHLC 데이터베이스 초기화 완료")

# API 요청 사이에 지연 시간을 추가하는 함수
async def rate_limited_request(url, headers=None):
    max_retries = 5
    retry_delay = 3
    
    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=headers)
            
            # 성공적인 응답이면 바로 반환
            if response.status_code != 429:
                return response
            
            # 429 오류(Rate Limit)인 경우 지연 후 재시도
            logger.warning(f"API 요청 제한 도달 (429). {retry_delay}초 후 재시도 ({attempt+1}/{max_retries})...")
            await asyncio.sleep(retry_delay)
            retry_delay *= 2  # 지수 백오프 적용
            
        except Exception as e:
            logger.error(f"API 요청 중 오류: {str(e)}")
            await asyncio.sleep(retry_delay)
            retry_delay *= 2
    
    # 모든 재시도 실패 시 마지막 응답 반환
    return response

# 토큰 가격 정보 조회
async def get_token_price(token_address: str, network: str = "ethereum") -> Dict[str, Any]:
    """
    토큰의 현재 가격 정보를 조회합니다.
    
    Args:
        token_address (str): 토큰 주소
        network (str, optional): 네트워크 이름. 기본값은 "ethereum"
        
    Returns:
        Dict[str, Any]: 가격 정보
    """
    try:
        # 네트워크 ID 변환
        api_network = NETWORK_MAPPING.get(network.lower(), network.lower())
        
        # API 엔드포인트 구성
        url = f"https://api.geckoterminal.com/api/v2/networks/{api_network}/tokens/{token_address}"
        headers = {"Accept": "application/json"}
        
        logger.info(f"API 요청: {url}")
        
        # 일반 requests.get 대신 rate_limited_request 사용
        response = await rate_limited_request(url, headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            if 'data' in data and 'attributes' in data['data']:
                attrs = data['data']['attributes']
                
                return {
                    "success": True,
                    "name": attrs.get('name', '알 수 없음'),
                    "symbol": attrs.get('symbol', '???'),
                    "price": float(attrs.get('price_usd') or 0),
                    "address": token_address,
                    "market_cap": float(attrs.get('market_cap_usd') or attrs.get('fdv_usd') or 0),
                    "volume_24h": float(attrs.get('volume_usd_24h') or 0),
                    "timestamp": datetime.now().isoformat()
                }
            else:
                logger.error(f"API 응답에 필요한 데이터가 없습니다: {data}")
                return {"success": False, "error": "API 응답에 필요한 데이터가 없습니다."}
        else:
            logger.error(f"API 응답 오류: 상태 코드 {response.status_code}, 응답: {response.text}")
            
            if response.status_code == 404:
                return {"success": False, "error": f"토큰을 찾을 수 없습니다. 주소가 올바른지, 네트워크가 맞는지 확인하세요."}
            
            return {"success": False, "error": f"토큰 정보를 찾을 수 없습니다. 상태 코드: {response.status_code}"}
    
    except Exception as e:
        logger.error(f"토큰 가격 조회 오류: {str(e)}")
        return {"success": False, "error": str(e)}

# 데이터베이스에서 모든 사용자의 토큰 목록 가져오기
def get_all_tokens() -> List[Tuple[int, str, str]]:
    """
    데이터베이스에서 모든 사용자의 토큰 목록을 가져옵니다.
    
    Returns:
        List[Tuple[int, str, str]]: (user_id, token_address, network) 형태의 튜플 리스트
    """
    conn = sqlite3.connect('tokens.db')
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, token, network FROM tokens")
    tokens = cursor.fetchall()
    conn.close()
    return tokens

# OHLC 데이터 저장
async def save_ohlc_data(token_address: str, network: str, price_data: Dict[str, Any], interval: str = "1h"):
    """
    토큰의 OHLC 데이터를 저장합니다.
    
    Args:
        token_address (str): 토큰 주소
        network (str): 네트워크 이름
        price_data (Dict[str, Any]): 가격 데이터
        interval (str, optional): 시간 간격. 기본값은 "1h"
    """
    try:
        if not price_data["success"]:
            return
        
        conn = sqlite3.connect('tokens.db')
        cursor = conn.cursor()
        
        # 현재 시간을 간격에 맞게 조정 (예: 1시간 간격이면 분, 초를 0으로)
        now = datetime.now()
        if interval == "1h":
            timestamp = datetime(now.year, now.month, now.day, now.hour).isoformat()
        elif interval == "1d":
            timestamp = datetime(now.year, now.month, now.day).isoformat()
        else:
            timestamp = now.isoformat()
        
        # 현재 간격의 기존 OHLC 데이터 확인
        cursor.execute(
            "SELECT open, high, low, close, volume FROM token_ohlc WHERE token_address = ? AND network = ? AND timestamp = ? AND interval = ?",
            (token_address, network, timestamp, interval)
        )
        existing_data = cursor.fetchone()
        
        current_price = price_data["price"]
        current_volume = price_data.get("volume_24h", 0)
        
        if existing_data:
            # 기존 데이터 업데이트
            open_price, high_price, low_price, close_price, volume = existing_data
            
            # 고가와 저가 업데이트
            high_price = max(high_price, current_price)
            low_price = min(low_price, current_price)
            
            cursor.execute(
                """
                UPDATE token_ohlc 
                SET high = ?, low = ?, close = ?, volume = ?
                WHERE token_address = ? AND network = ? AND timestamp = ? AND interval = ?
                """,
                (high_price, low_price, current_price, current_volume, token_address, network, timestamp, interval)
            )
        else:
            # 새 데이터 삽입
            cursor.execute(
                """
                INSERT INTO token_ohlc (token_address, network, timestamp, open, high, low, close, volume, interval)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (token_address, network, timestamp, current_price, current_price, current_price, current_price, current_volume, interval)
            )
        
        conn.commit()
        conn.close()
        
    except Exception as e:
        logger.error(f"OHLC 데이터 저장 중 오류: {str(e)}")

# OHLC 데이터 조회
def get_ohlc_data(token_address: str, network: str, interval: str = "1h", limit: int = 24) -> Dict[str, Any]:
    """
    토큰의 OHLC 데이터를 조회합니다.
    
    Args:
        token_address (str): 토큰 주소
        network (str): 네트워크 이름
        interval (str, optional): 시간 간격. 기본값은 "1h"
        limit (int, optional): 조회할 데이터 개수. 기본값은 24
        
    Returns:
        Dict[str, Any]: OHLC 데이터
    """
    try:
        conn = sqlite3.connect('tokens.db')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute(
            """
            SELECT * FROM token_ohlc 
            WHERE token_address = ? AND network = ? AND interval = ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (token_address, network, interval, limit)
        )
        
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            return {
                "success": False,
                "error": "OHLC 데이터가 없습니다."
            }
        
        ohlc_data = []
        for row in rows:
            ohlc_data.append({
                "timestamp": row["timestamp"],
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
                "volume": row["volume"]
            })
        
        return {
            "success": True,
            "data": ohlc_data
        }
    
    except Exception as e:
        logger.error(f"OHLC 데이터 조회 오류: {str(e)}")
        return {
            "success": False,
            "error": str(e)
        }

# 일일 가격 변동률 계산
def calculate_daily_change(token_address: str, network: str) -> Dict[str, Any]:
    """
    토큰의 일일 가격 변동률을 계산합니다.
    
    Args:
        token_address (str): 토큰 주소
        network (str): 네트워크 이름
        
    Returns:
        Dict[str, Any]: 일일 가격 변동률 정보
    """
    try:
        conn = sqlite3.connect('tokens.db')
        cursor = conn.cursor()
        
        # 오늘의 OHLC 데이터 조회
        today = datetime.now().strftime("%Y-%m-%d")
        cursor.execute(
            """
            SELECT open, close FROM token_ohlc 
            WHERE token_address = ? AND network = ? AND interval = '1d'
            AND timestamp LIKE ?
            ORDER BY timestamp DESC
            LIMIT 1
            """,
            (token_address, network, f"{today}%")
        )
        
        today_data = cursor.fetchone()
        
        # 어제의 OHLC 데이터 조회
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        cursor.execute(
            """
            SELECT close FROM token_ohlc 
            WHERE token_address = ? AND network = ? AND interval = '1d'
            AND timestamp LIKE ?
            ORDER BY timestamp DESC
            LIMIT 1
            """,
            (token_address, network, f"{yesterday}%")
        )
        
        yesterday_data = cursor.fetchone()
        conn.close()
        
        if not today_data:
            return {
                "success": False,
                "error": "오늘의 OHLC 데이터가 없습니다."
            }
        
        # 오늘의 시가와 현재가 사용
        open_price = today_data[0]
        current_price = today_data[1]
        
        # 어제의 종가가 있으면 사용, 없으면 오늘의 시가 사용
        if yesterday_data:
            yesterday_close = yesterday_data[0]
            daily_change = ((current_price - yesterday_close) / yesterday_close) * 100
        else:
            daily_change = ((current_price - open_price) / open_price) * 100
        
        return {
            "success": True,
            "daily_change": daily_change,
            "open_price": open_price,
            "current_price": current_price
        }
    
    except Exception as e:
        logger.error(f"일일 가격 변동률 계산 오류: {str(e)}")
        return {
            "success": False,
            "error": str(e)
        }

# OHLC 알림 설정 추가
def add_ohlc_alert(user_id: int, token_address: str, network: str, alert_type: str, threshold: float) -> bool:
    """
    사용자의 OHLC 알림 설정을 추가합니다.
    
    Args:
        user_id (int): 사용자 ID
        token_address (str): 토큰 주소
        network (str): 네트워크 이름
        alert_type (str): 알림 유형 (price_above, price_below, daily_change)
        threshold (float): 임계값
        
    Returns:
        bool: 성공 여부
    """
    try:
        conn = sqlite3.connect('tokens.db')
        cursor = conn.cursor()
        
        cursor.execute(
            """
            INSERT OR REPLACE INTO ohlc_alerts 
            (user_id, token_address, network, alert_type, threshold, enabled, last_alert)
            VALUES (?, ?, ?, ?, ?, 1, NULL)
            """,
            (user_id, token_address, network, alert_type, threshold)
        )
        
        conn.commit()
        conn.close()
        
        logger.info(f"OHLC 알림 설정 추가 성공 (사용자 ID: {user_id}, 토큰: {token_address}, 유형: {alert_type})")
        return True
    
    except Exception as e:
        logger.error(f"OHLC 알림 설정 추가 오류: {str(e)}")
        return False

# OHLC 알림 설정 제거
def remove_ohlc_alert(user_id: int, token_address: str, network: str, alert_type: str) -> bool:
    """
    사용자의 OHLC 알림 설정을 제거합니다.
    
    Args:
        user_id (int): 사용자 ID
        token_address (str): 토큰 주소
        network (str): 네트워크 이름
        alert_type (str): 알림 유형
        
    Returns:
        bool: 성공 여부
    """
    try:
        conn = sqlite3.connect('tokens.db')
        cursor = conn.cursor()
        
        cursor.execute(
            """
            DELETE FROM ohlc_alerts 
            WHERE user_id = ? AND token_address = ? AND network = ? AND alert_type = ?
            """,
            (user_id, token_address, network, alert_type)
        )
        
        conn.commit()
        conn.close()
        
        logger.info(f"OHLC 알림 설정 제거 성공 (사용자 ID: {user_id}, 토큰: {token_address}, 유형: {alert_type})")
        return True
    
    except Exception as e:
        logger.error(f"OHLC 알림 설정 제거 오류: {str(e)}")
        return False

# 사용자의 OHLC 알림 설정 목록 조회
def get_user_ohlc_alerts(user_id: int) -> List[Dict[str, Any]]:
    """
    사용자의 OHLC 알림 설정 목록을 조회합니다.
    
    Args:
        user_id (int): 사용자 ID
        
    Returns:
        List[Dict[str, Any]]: 알림 설정 목록
    """
    try:
        conn = sqlite3.connect('tokens.db')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute(
            """
            SELECT a.*, t.name, t.symbol FROM ohlc_alerts a
            LEFT JOIN (
                SELECT token as token_address, network, MAX(name) as name, MAX(symbol) as symbol
                FROM tokens
                GROUP BY token_address, network
            ) t ON a.token_address = t.token_address AND a.network = t.network
            WHERE a.user_id = ? AND a.enabled = 1
            """,
            (user_id,)
        )
        
        rows = cursor.fetchall()
        conn.close()
        
        alerts = []
        for row in rows:
            alerts.append({
                "token_address": row["token_address"],
                "network": row["network"],
                "alert_type": row["alert_type"],
                "threshold": row["threshold"],
                "name": row["name"] if row["name"] else "알 수 없음",
                "symbol": row["symbol"] if row["symbol"] else "???"
            })
        
        return alerts
    
    except Exception as e:
        logger.error(f"OHLC 알림 설정 목록 조회 오류: {str(e)}")
        return []

# 토큰의 OHLC 차트 데이터 생성
def generate_ohlc_chart_data(token_address: str, network: str, interval: str = "1h", limit: int = 24) -> Dict[str, Any]:
    """
    토큰의 OHLC 차트 데이터를 생성합니다.
    
    Args:
        token_address (str): 토큰 주소
        network (str): 네트워크 이름
        interval (str, optional): 시간 간격. 기본값은 "1h"
        limit (int, optional): 조회할 데이터 개수. 기본값은 24
        
    Returns:
        Dict[str, Any]: 차트 데이터
    """
    ohlc_data = get_ohlc_data(token_address, network, interval, limit)
    
    if not ohlc_data["success"]:
        return {
            "success": False,
            "error": ohlc_data["error"]
        }
    
    # 차트 데이터 형식으로 변환
    chart_data = {
        "timestamps": [],
        "opens": [],
        "highs": [],
        "lows": [],
        "closes": [],
        "volumes": []
    }
    
    for candle in reversed(ohlc_data["data"]):  # 시간순 정렬
        chart_data["timestamps"].append(candle["timestamp"])
        chart_data["opens"].append(candle["open"])
        chart_data["highs"].append(candle["high"])
        chart_data["lows"].append(candle["low"])
        chart_data["closes"].append(candle["close"])
        chart_data["volumes"].append(candle["volume"])
    
    return {
        "success": True,
        "chart_data": chart_data
    }

# 토큰 가격 요약 정보 생성
def generate_price_summary(token_address: str, network: str) -> Dict[str, Any]:
    """
    토큰의 가격 요약 정보를 생성합니다.
    
    Args:
        token_address (str): 토큰 주소
        network (str): 네트워크 이름
        
    Returns:
        Dict[str, Any]: 가격 요약 정보
    """
    try:
        # 일일 OHLC 데이터 조회
        daily_ohlc = get_ohlc_data(token_address, network, "1d", 7)
        
        if not daily_ohlc["success"] or not daily_ohlc["data"]:
            return {
                "success": False,
                "error": "가격 데이터가 부족합니다."
            }
        
        # 현재 가격 (최신 종가)
        current_price = daily_ohlc["data"][0]["close"]
        
        # 일일 변동률 계산
        daily_change = calculate_daily_change(token_address, network)
        daily_change_percent = daily_change["daily_change"] if daily_change["success"] else 0
        
        # 주간 고가/저가 계산
        weekly_high = max([candle["high"] for candle in daily_ohlc["data"]])
        weekly_low = min([candle["low"] for candle in daily_ohlc["data"]])
        
        # 주간 변동률 계산 (7일 전 종가 대비)
        if len(daily_ohlc["data"]) >= 7:
            week_ago_close = daily_ohlc["data"][-1]["close"]
            weekly_change_percent = ((current_price - week_ago_close) / week_ago_close) * 100
        else:
            weekly_change_percent = 0
        
        return {
            "success": True,
            "current_price": current_price,
            "daily_change_percent": daily_change_percent,
            "weekly_high": weekly_high,
            "weekly_low": weekly_low,
            "weekly_change_percent": weekly_change_percent
        }
    
    except Exception as e:
        logger.error(f"가격 요약 정보 생성 오류: {str(e)}")
        return {
            "success": False,
            "error": str(e)
        }

# OHLC 데이터 수집 및 알림 처리
async def collect_ohlc_data_and_check_alerts(bot=None):
    """
    모든 토큰의 OHLC 데이터를 수집하고 알림 조건을 확인합니다.
    
    Args:
        bot: 텔레그램 봇 객체 (알림 전송용)
    """
    try:
        # 모든 토큰 목록 가져오기
        tokens = get_all_tokens()
        
        # 토큰별로 중복 제거 (여러 사용자가 같은 토큰을 추적할 수 있음)
        unique_tokens = {}
        for user_id, token_address, network in tokens:
            key = f"{token_address}_{network}"
            if key not in unique_tokens:
                unique_tokens[key] = (token_address, network)
        
        logger.info(f"OHLC 데이터 수집 시작: {len(unique_tokens)}개 토큰")
        
        # 각 토큰의 가격 정보 수집 및 OHLC 데이터 저장
        for token_address, network in unique_tokens.values():
            try:
                # API 요청 사이에 지연 시간 추가
                await asyncio.sleep(1)
                
                # 토큰 가격 조회
                price_info = await get_token_price(token_address, network)
                
                if not price_info["success"]:
                    logger.error(f"토큰 {token_address} 가격 조회 실패: {price_info['error']}")
                    continue
                
                # OHLC 데이터 저장 (1시간 및 1일 간격)
                await save_ohlc_data(token_address, network, price_info, "1h")
                await save_ohlc_data(token_address, network, price_info, "1d")
                
                # 알림 처리 (봇이 제공된 경우)
                if bot:
                    await check_ohlc_alerts(bot, token_address, network, price_info)
                
            except Exception as e:
                logger.error(f"토큰 {token_address} ({network}) OHLC 데이터 수집 중 오류: {str(e)}")
                continue
        
        logger.info(f"OHLC 데이터 수집 완료")
        
    except Exception as e:
        logger.error(f"OHLC 데이터 수집 및 알림 처리 중 오류: {str(e)}")

# OHLC 알림 조건 확인 및 알림 전송
async def check_ohlc_alerts(bot, token_address: str, network: str, price_info: Dict[str, Any]):
    """
    토큰의 OHLC 알림 조건을 확인하고 알림을 전송합니다.
    
    Args:
        bot: 텔레그램 봇 객체
        token_address (str): 토큰 주소
        network (str): 네트워크 이름
        price_info (Dict[str, Any]): 현재 가격 정보
    """
    try:
        conn = sqlite3.connect('tokens.db')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # 해당 토큰에 대한 모든 알림 설정 가져오기
        cursor.execute(
            """
            SELECT * FROM ohlc_alerts 
            WHERE token_address = ? AND network = ? AND enabled = 1
            """,
            (token_address, network)
        )
        
        alerts = cursor.fetchall()
        
        # 현재 시간
        now = datetime.now()
        
        for alert in alerts:
            user_id = alert["user_id"]
            alert_type = alert["alert_type"]
            threshold = alert["threshold"]
            last_alert = alert["last_alert"]
            
            # 마지막 알림 시간 확인 (너무 자주 알림이 가지 않도록)
            if last_alert:
                last_alert_time = datetime.fromisoformat(last_alert)
                if (now - last_alert_time).total_seconds() < 3600:  # 1시간에 한 번만 알림
                    continue
            
            # 알림 조건 확인
            alert_triggered = False
            alert_message = ""
            
            if alert_type == "price_above" and price_info["price"] >= threshold:
                alert_triggered = True
                
                # 시가총액 정보 추가
                market_cap_text = ""
                if "market_cap" in price_info and isinstance(price_info["market_cap"], (int, float)) and price_info["market_cap"] > 0:
                    market_cap = price_info["market_cap"]
                    market_cap_formatted = f"${market_cap:,.0f}"
                    market_cap_text = f"시가총액: <b>{market_cap_formatted}</b>\n"
                
                alert_message = (
                    f"🚀 <b>가격 상승 알림!</b>\n\n"
                    f"<b>{price_info['name']} ({price_info['symbol']})</b>\n"
                    f"네트워크: <code>{network}</code>\n"
                    f"현재 가격: <b>${price_info['price']:.8f}</b>\n"
                    f"설정 가격: <b>${threshold:.8f}</b>\n"
                    f"{market_cap_text}\n"
                    f"🕒 {now.strftime('%Y-%m-%d %H:%M:%S')}"
                )
            
            elif alert_type == "price_below" and price_info["price"] <= threshold:
                alert_triggered = True
                
                # 시가총액 정보 추가
                market_cap_text = ""
                if "market_cap" in price_info and isinstance(price_info["market_cap"], (int, float)) and price_info["market_cap"] > 0:
                    market_cap = price_info["market_cap"]
                    market_cap_formatted = f"${market_cap:,.0f}"
                    market_cap_text = f"시가총액: <b>{market_cap_formatted}</b>\n"
                
                alert_message = (
                    f"📉 <b>가격 하락 알림!</b>\n\n"
                    f"<b>{price_info['name']} ({price_info['symbol']})</b>\n"
                    f"네트워크: <code>{network}</code>\n"
                    f"현재 가격: <b>${price_info['price']:.8f}</b>\n"
                    f"설정 가격: <b>${threshold:.8f}</b>\n"
                    f"{market_cap_text}\n"
                    f"🕒 {now.strftime('%Y-%m-%d %H:%M:%S')}"
                )
            
            elif alert_type == "daily_change":
                # 일일 가격 변동 계산
                daily_change = calculate_daily_change(token_address, network)
                
                if daily_change["success"]:
                    change_percent = daily_change["daily_change"]
                    
                    # 변동률이 임계값을 초과하는지 확인
                    if abs(change_percent) >= threshold:
                        alert_triggered = True
                        change_emoji = "🚀" if change_percent > 0 else "📉"
                        change_direction = "상승" if change_percent > 0 else "하락"
                        
                        # 시가총액 정보 추가
                        market_cap_text = ""
                        if "market_cap" in price_info and isinstance(price_info["market_cap"], (int, float)) and price_info["market_cap"] > 0:
                            market_cap = price_info["market_cap"]
                            market_cap_formatted = f"${market_cap:,.0f}"
                            market_cap_text = f"시가총액: <b>{market_cap_formatted}</b>\n"
                        
                        alert_message = (
                            f"{change_emoji} <b>일일 가격 변동 알림!</b>\n\n"
                            f"<b>{price_info['name']} ({price_info['symbol']})</b>\n"
                            f"네트워크: <code>{network}</code>\n"
                            f"현재 가격: <b>${price_info['price']:.8f}</b>\n"
                            f"일일 변동: <b>{change_percent:.2f}% {change_direction}</b>\n"
                            f"설정 임계값: <b>{threshold:.2f}%</b>\n"
                            f"{market_cap_text}\n"
                            f"🕒 {now.strftime('%Y-%m-%d %H:%M:%S')}"
                        )
            
            # 알림 전송
            if alert_triggered:
                try:
                    await bot.send_message(
                        user_id,
                        alert_message,
                        parse_mode="HTML"
                    )
                    
                    # 마지막 알림 시간 업데이트
                    cursor.execute(
                        """
                        UPDATE ohlc_alerts 
                        SET last_alert = ?
                        WHERE user_id = ? AND token_address = ? AND network = ? AND alert_type = ?
                        """,
                        (now.isoformat(), user_id, token_address, network, alert_type)
                    )
                    
                    logger.info(f"OHLC 알림 전송 성공 (사용자 ID: {user_id}, 토큰: {price_info['symbol']}, 유형: {alert_type})")
                
                except Exception as e:
                    logger.error(f"OHLC 알림 전송 실패 (사용자 ID: {user_id}): {str(e)}")
        
        conn.commit()
        conn.close()
    
    except Exception as e:
        logger.error(f"OHLC 알림 확인 중 오류: {str(e)}")

# OHLC 데이터 수집 스케줄러
async def ohlc_scheduler(bot=None, interval_seconds=300):
    """
    OHLC 데이터 수집 및 알림 처리를 주기적으로 실행하는 스케줄러입니다.
    
    Args:
        bot: 텔레그램 봇 객체 (알림 전송용)
        interval_seconds (int, optional): 실행 간격(초). 기본값은 300초(5분)
    """
    # 데이터베이스 초기화
    init_ohlc_db()
    
    while True:
        try:
            # OHLC 데이터 수집 및 알림 처리
            await collect_ohlc_data_and_check_alerts(bot)
            
            # 다음 실행까지 대기
            await asyncio.sleep(interval_seconds)
            
        except Exception as e:
            logger.error(f"OHLC 스케줄러 실행 중 오류: {str(e)}")
            await asyncio.sleep(60)  # 오류 발생 시 1분 대기 후 재시도

# 메인 함수 (테스트용)
async def main():
    # 데이터베이스 초기화
    init_ohlc_db()
    
    # 테스트 토큰 (이더리움 네트워크의 USDT)
    token_address = "0xdac17f958d2ee523a2206206994597c13d831ec7"
    network = "ethereum"
    
    # 토큰 가격 조회 테스트
    price_info = await get_token_price(token_address, network)
    print(f"토큰 가격 정보: {price_info}")
    
    # OHLC 데이터 저장 테스트
    await save_ohlc_data(token_address, network, price_info, "1h")
    await save_ohlc_data(token_address, network, price_info, "1d")
    
    # OHLC 데이터 조회 테스트
    ohlc_data = get_ohlc_data(token_address, network, "1h", 5)
    print(f"OHLC 데이터: {ohlc_data}")
    
    # 일일 가격 변동률 계산 테스트
    daily_change = calculate_daily_change(token_address, network)
    print(f"일일 가격 변동률: {daily_change}")

# 일일 요약 알림 데이터베이스 초기화
def init_daily_summary_db():
    """
    일일 요약 알림 설정을 저장할 데이터베이스 테이블을 초기화합니다.
    """
    conn = sqlite3.connect('tokens.db')
    cursor = conn.cursor()
    
    # 일일 요약 알림 설정 테이블 생성
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS daily_summary_alerts (
        user_id INTEGER PRIMARY KEY,
        enabled INTEGER DEFAULT 1
    )
    ''')
    
    conn.commit()
    conn.close()
    logger.info("일일 요약 알림 데이터베이스 초기화 완료")

# 일일 요약 알림 활성화
def enable_daily_summary_alerts(user_id: int) -> bool:
    """
    사용자의 일일 요약 알림을 활성화합니다.
    
    Args:
        user_id (int): 사용자 ID
        
    Returns:
        bool: 성공 여부
    """
    try:
        conn = sqlite3.connect('tokens.db')
        cursor = conn.cursor()
        
        cursor.execute(
            "INSERT OR REPLACE INTO daily_summary_alerts (user_id, enabled) VALUES (?, 1)",
            (user_id,)
        )
        
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"일일 요약 알림 활성화 중 오류: {str(e)}")
        return False

# 일일 요약 알림 비활성화
def disable_daily_summary_alerts(user_id: int) -> bool:
    """
    사용자의 일일 요약 알림을 비활성화합니다.
    
    Args:
        user_id (int): 사용자 ID
        
    Returns:
        bool: 성공 여부
    """
    try:
        conn = sqlite3.connect('tokens.db')
        cursor = conn.cursor()
        
        cursor.execute(
            "INSERT OR REPLACE INTO daily_summary_alerts (user_id, enabled) VALUES (?, 0)",
            (user_id,)
        )
        
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"일일 요약 알림 비활성화 중 오류: {str(e)}")
        return False

# 일일 요약 알림 상태 확인
def get_daily_summary_alerts_status(user_id: int) -> bool:
    """
    사용자의 일일 요약 알림 활성화 상태를 확인합니다.
    
    Args:
        user_id (int): 사용자 ID
        
    Returns:
        bool: 활성화 상태 (True: 활성화, False: 비활성화)
    """
    try:
        conn = sqlite3.connect('tokens.db')
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT enabled FROM daily_summary_alerts WHERE user_id = ?",
            (user_id,)
        )
        
        result = cursor.fetchone()
        conn.close()
        
        if result is None:
            return False
        
        return bool(result[0])
    except Exception as e:
        logger.error(f"일일 요약 알림 상태 확인 중 오류: {str(e)}")
        return False

# 일일 요약 알림 전송
async def send_daily_summary_alerts(bot):
    """
    모든 사용자에게 추적 중인 토큰의 일일 요약 정보를 전송합니다.
    매일 오전 6:00에 실행됩니다.
    """
    try:
        logger.info("일일 요약 알림 전송 시작")
        
        conn = sqlite3.connect('tokens.db')
        cursor = conn.cursor()
        
        # 알림을 받을 사용자 목록 조회
        cursor.execute(
            """
            SELECT user_id FROM daily_summary_alerts 
            WHERE enabled = 1
            """
        )
        users = cursor.fetchall()
        
        if not users:
            logger.info("일일 요약 알림을 받을 사용자가 없습니다.")
            conn.close()
            return
        
        for (user_id,) in users:
            # 사용자가 추적 중인 토큰 목록 조회
            cursor.execute(
                """
                SELECT token, network FROM tokens 
                WHERE user_id = ?
                """,
                (user_id,)
            )
            user_tokens = cursor.fetchall()
            
            if not user_tokens:
                logger.info(f"사용자 {user_id}가 추적 중인 토큰이 없습니다.")
                continue
            
            # 일일 요약 메시지 생성
            summary_message = (
                "📊 <b>일일 토큰 요약 보고서</b>\n\n"
                f"🕒 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
            )
            
            for token_address, network in user_tokens:
                try:
                    # 토큰 정보 조회
                    price_info = await get_token_price(token_address, network)
                    if not price_info["success"]:
                        continue
                    
                    # 일일 변동률 계산
                    daily_change = calculate_daily_change(token_address, network)
                    
                    # OHLC 데이터 조회 (최근 24시간)
                    ohlc_data = get_ohlc_data(token_address, network, "1d", 1)
                    
                    # 토큰 요약 정보 추가
                    token_summary = (
                        f"<b>{price_info['name']} ({price_info['symbol']})</b>\n"
                        f"네트워크: <code>{network}</code>\n"
                        f"현재 가격: <b>${price_info['price']:.8f}</b>\n"
                    )
                    
                    # 일일 변동률 추가
                    if daily_change["success"]:
                        change_emoji = "🚀" if daily_change["daily_change"] > 0 else "📉"
                        change_direction = "상승" if daily_change["daily_change"] > 0 else "하락"
                        
                        token_summary += (
                            f"일일 변동: <b>{change_emoji} {daily_change['daily_change']:.2f}% {change_direction}</b>\n"
                        )
                    
                    # OHLC 데이터 추가
                    if ohlc_data["success"] and ohlc_data["data"]:
                        candle = ohlc_data["data"][0]
                        token_summary += (
                            f"24시간 시가: <b>${candle['open']:.8f}</b>\n"
                            f"24시간 고가: <b>${candle['high']:.8f}</b>\n"
                            f"24시간 저가: <b>${candle['low']:.8f}</b>\n"
                            f"24시간 종가: <b>${candle['close']:.8f}</b>\n"
                        )
                    
                    # 거래량 추가 (있는 경우)
                    if ohlc_data["success"] and ohlc_data["data"] and "volume" in ohlc_data["data"][0]:
                        token_summary += f"24시간 거래량: <b>${ohlc_data['data'][0]['volume']:,.2f}</b>\n"
                    
                    # 차트 링크 추가
                    token_summary += (
                        f"\n<a href='https://www.geckoterminal.com/{network}/tokens/{token_address}'>GeckoTerminal에서 차트 보기</a>\n\n"
                    )
                    
                    summary_message += token_summary
                    
                except Exception as e:
                    logger.error(f"토큰 {token_address} ({network}) 요약 정보 생성 중 오류: {str(e)}")
            
            # 알림 전송
            try:
                await bot.send_message(
                    user_id,
                    summary_message,
                    parse_mode="HTML",
                    disable_web_page_preview=True
                )
                logger.info(f"사용자 {user_id}에게 일일 요약 알림 전송 완료")
            except Exception as e:
                logger.error(f"사용자 {user_id}에게 일일 요약 알림 전송 실패: {str(e)}")
        
        conn.close()
        logger.info("일일 요약 알림 전송 완료")
    
    except Exception as e:
        logger.error(f"일일 요약 알림 처리 중 오류: {str(e)}")

# 일일 요약 알림 스케줄러
async def daily_summary_scheduler(bot):
    """
    매일 오전 6:00에 일일 요약 알림을 전송하는 스케줄러입니다.
    """
    # 데이터베이스 초기화
    init_daily_summary_db()
    
    while True:
        try:
            # 현재 시간
            now = datetime.now()
            
            # 다음 오전 6시 계산
            target_time = now.replace(hour=6, minute=0, second=0, microsecond=0)
            if now >= target_time:
                # 이미 오늘의 오전 6시가 지났으면 내일 오전 6시로 설정
                target_time = target_time + timedelta(days=1)
            
            # 다음 실행까지 대기 시간 계산 (초 단위)
            wait_seconds = (target_time - now).total_seconds()
            
            logger.info(f"다음 일일 요약 알림 전송까지 {wait_seconds:.0f}초 대기 ({target_time.strftime('%Y-%m-%d %H:%M:%S')})")
            
            # 대기 후 실행
            await asyncio.sleep(wait_seconds)
            
            # 일일 요약 알림 전송
            await send_daily_summary_alerts(bot)
            
        except Exception as e:
            logger.error(f"일일 요약 알림 스케줄러 실행 중 오류: {str(e)}")
            await asyncio.sleep(60)  # 오류 발생 시 1분 대기 후 재시도

if __name__ == "__main__":
    asyncio.run(main())