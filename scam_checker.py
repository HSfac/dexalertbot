import logging
import requests
from datetime import datetime

# 로깅 설정
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('scam_checker')

# 네트워크 ID 매핑
NETWORK_MAPPING = {
    "ethereum": "eth",
    "bsc": "bsc",
    "polygon": "polygon_pos",
    "arbitrum": "arbitrum",
    "solana": "solana",
    "avalanche": "avax",
    "optimism": "optimism",
    "base": "base"
}

# 토큰 기본 정보 조회
async def get_token_info(token_address, network="ethereum"):
    try:
        # 네트워크 ID 변환
        api_network = NETWORK_MAPPING.get(network.lower(), network.lower())
        
        # API 엔드포인트 구성
        url = f"https://api.geckoterminal.com/api/v2/networks/{api_network}/tokens/{token_address}"
        headers = {"Accept": "application/json"}
        
        logger.info(f"토큰 정보 API 요청: {url}")
        response = requests.get(url, headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            if 'data' in data and 'attributes' in data['data']:
                attrs = data['data']['attributes']
                
                # 모든 가능한 데이터 추출
                result = {
                    "success": True,
                    "name": attrs.get('name', '알 수 없음'),
                    "symbol": attrs.get('symbol', '???'),
                    "price": float(attrs.get('price_usd') or 0),
                    "address": token_address,
                    "decimals": int(attrs.get('decimals') or 0),
                    "created_at": attrs.get('pool_created_at') or attrs.get('created_at'),
                    "market_cap": float(attrs.get('market_cap_usd') or attrs.get('fdv_usd') or 0),
                    "total_supply": float(attrs.get('total_supply') or 0),
                    "circulating_supply": float(attrs.get('circulating_supply') or 0),
                    "total_reserve_in_usd": float(attrs.get('total_reserve_in_usd') or 0),
                    "coingecko_coin_id": attrs.get('coingecko_coin_id')
                }
                
                # 추가 정보 조회 (token_info 엔드포인트)
                token_info_url = f"https://api.geckoterminal.com/api/v2/networks/{api_network}/tokens/{token_address}/info"
                token_info_response = requests.get(token_info_url, headers=headers)
                
                if token_info_response.status_code == 200:
                    token_info_data = token_info_response.json()
                    if 'data' in token_info_data and 'attributes' in token_info_data['data']:
                        info_attrs = token_info_data['data']['attributes']
                        
                        # 소셜 미디어 정보 추가
                        result["twitter_url"] = f"https://twitter.com/{info_attrs.get('twitter_handle')}" if info_attrs.get('twitter_handle') else None
                        result["telegram_url"] = f"https://t.me/{info_attrs.get('telegram_handle')}" if info_attrs.get('telegram_handle') else None
                        result["discord_url"] = info_attrs.get('discord_url')
                        result["website_url"] = info_attrs.get('websites', [None])[0] if info_attrs.get('websites') else None
                        result["description"] = info_attrs.get('description')
                        result["image_url"] = info_attrs.get('image_url')
                        result["gt_score"] = float(info_attrs.get('gt_score') or 0)
                        result["categories"] = info_attrs.get('categories', [])
                
                return result
            else:
                logger.error(f"API 응답에 필요한 데이터가 없습니다: {data}")
                return {"success": False, "error": "API 응답에 필요한 데이터가 없습니다"}
        else:
            logger.error(f"API 응답 오류: 상태 코드 {response.status_code}, 응답: {response.text}")
            
            if response.status_code == 404:
                return {"success": False, "error": f"토큰을 찾을 수 없습니다. 주소가 올바른지, 네트워크가 맞는지 확인하세요."}
            
            return {"success": False, "error": f"토큰 정보를 찾을 수 없습니다. 상태 코드: {response.status_code}"}
    
    except Exception as e:
        logger.error(f"토큰 정보 조회 오류: {str(e)}")
        return {"success": False, "error": str(e)}

# 토큰 유동성 풀 조회
async def get_token_pools(token_address, network="ethereum"):
    try:
        # 네트워크 ID 변환
        api_network = NETWORK_MAPPING.get(network.lower(), network.lower())
        
        # API 엔드포인트 구성
        url = f"https://api.geckoterminal.com/api/v2/networks/{api_network}/tokens/{token_address}/pools"
        headers = {"Accept": "application/json"}
        
        logger.info(f"유동성 풀 API 요청: {url}")
        response = requests.get(url, headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            if 'data' in data:
                pools_data = []
                
                for pool in data['data']:
                    if 'attributes' in pool:
                        attrs = pool['attributes']
                        pool_info = {
                            "address": attrs.get('address', '알 수 없음'),
                            "name": attrs.get('name', '알 수 없음'),
                            "dex": attrs.get('dex_id', '알 수 없음').replace('_', ' ').title(),
                            "liquidity": float(attrs.get('reserve_in_usd') or 0),
                            "volume_24h": float(attrs.get('volume_usd_24h') or 0),
                            "transactions_24h": int(attrs.get('transactions_24h') or 0),
                            "price_change_24h": float(attrs.get('price_change_percentage_24h') or 0)
                        }
                        pools_data.append(pool_info)
                
                return {
                    "success": True,
                    "data": pools_data
                }
            else:
                return {"success": False, "error": "API 응답에 필요한 데이터가 없습니다"}
        else:
            if response.status_code == 404:
                return {"success": False, "error": "토큰을 찾을 수 없습니다"}
            
            return {"success": False, "error": f"API 오류: 상태 코드 {response.status_code}"}
    
    except Exception as e:
        logger.error(f"유동성 풀 조회 오류: {str(e)}")
        return {"success": False, "error": str(e)}

# 토큰 홀더 정보 조회
async def get_token_holders(token_address, network="ethereum", limit=10):
    try:
        # 네트워크 ID 변환
        api_network = NETWORK_MAPPING.get(network.lower(), network.lower())
        
        # API 엔드포인트 구성
        url = f"https://api.geckoterminal.com/api/v2/networks/{api_network}/tokens/{token_address}/holders"
        headers = {"Accept": "application/json"}
        params = {"page": 1, "limit": limit}
        
        logger.info(f"홀더 정보 API 요청: {url}")
        response = requests.get(url, headers=headers, params=params)
        
        if response.status_code == 200:
            data = response.json()
            if 'data' in data:
                holders_data = []
                
                for holder in data['data']:
                    if 'attributes' in holder:
                        attrs = holder['attributes']
                        holder_info = {
                            "address": attrs.get('address', '알 수 없음'),
                            "balance": float(attrs.get('balance', 0) or 0),
                            "percentage": float(attrs.get('percentage', 0) or 0),
                            "is_contract": bool(attrs.get('is_contract', False))
                        }
                        holders_data.append(holder_info)
                
                return {
                    "success": True,
                    "data": holders_data
                }
            else:
                return {"success": False, "error": "API 응답에 필요한 데이터가 없습니다"}
        else:
            if response.status_code == 404:
                return {"success": False, "error": "토큰을 찾을 수 없습니다"}
            
            return {"success": False, "error": f"API 오류: 상태 코드 {response.status_code}"}
    
    except Exception as e:
        logger.error(f"홀더 정보 조회 오류: {str(e)}")
        return {"success": False, "error": str(e)}

# 토큰 거래 내역 조회
async def get_token_trades(token_address, network="ethereum", limit=20):
    try:
        # 네트워크 ID 변환
        api_network = NETWORK_MAPPING.get(network.lower(), network.lower())
        
        # API 엔드포인트 구성
        url = f"https://api.geckoterminal.com/api/v2/networks/{api_network}/tokens/{token_address}/trades"
        headers = {"Accept": "application/json"}
        params = {"page": 1, "limit": limit}
        
        logger.info(f"거래 내역 API 요청: {url}")
        response = requests.get(url, headers=headers, params=params)
        
        if response.status_code == 200:
            data = response.json()
            if 'data' in data:
                trades_data = []
                
                for trade in data['data']:
                    if 'attributes' in trade:
                        attrs = trade['attributes']
                        trade_info = {
                            "timestamp": attrs.get('timestamp'),
                            "type": attrs.get('type', '알 수 없음'),
                            "amount_usd": float(attrs.get('amount_usd', 0) or 0),
                            "tx_hash": attrs.get('tx_hash', '알 수 없음'),
                            "pool_name": attrs.get('pool_name', '알 수 없음')
                        }
                        trades_data.append(trade_info)
                
                return {
                    "success": True,
                    "data": trades_data
                }
            else:
                return {"success": False, "error": "API 응답에 필요한 데이터가 없습니다"}
        else:
            if response.status_code == 404:
                return {"success": False, "error": "토큰을 찾을 수 없습니다"}
            
            return {"success": False, "error": f"API 오류: 상태 코드 {response.status_code}"}
    
    except Exception as e:
        logger.error(f"거래 내역 조회 오류: {str(e)}")
        return {"success": False, "error": str(e)}

# 스캠 토큰 탐지 함수
async def check_token_scam(token_address, network="ethereum"):
    try:
        # 1. 기본 토큰 정보 조회
        token_info = await get_token_info(token_address, network)
        if not token_info["success"]:
            return {"success": False, "error": token_info["error"]}
        
        # 2. 유동성 풀 정보 조회
        pools_info = await get_token_pools(token_address, network)
        
        # 3. 홀더 정보 조회
        holders_info = await get_token_holders(token_address, network, limit=20)
        
        # 4. 거래 내역 조회
        trades_info = await get_token_trades(token_address, network, limit=50)
        
        # 스캠 점수 초기화 (0-100, 높을수록 스캠 가능성 높음)
        scam_score = 0
        scam_indicators = []
        
        # 5. 스캠 지표 분석
        
        # 5.1 토큰 기본 정보 분석
        if not token_info.get("website_url") and not token_info.get("twitter_url") and not token_info.get("telegram_url"):
            scam_score += 20
            scam_indicators.append("소셜 미디어 및 웹사이트 정보 없음")
        
        # 5.2 유동성 분석
        total_liquidity = 0
        if pools_info["success"] and pools_info["data"]:
            total_liquidity = sum(pool["liquidity"] for pool in pools_info["data"])
            
            # 유동성이 매우 낮은 경우
            if total_liquidity < 5000:  # $5,000 미만
                scam_score += 15
                scam_indicators.append(f"매우 낮은 유동성 (${total_liquidity:.2f})")
        else:
            scam_score += 10
            scam_indicators.append("유동성 풀 정보 없음")
        
        # 5.3 홀더 분석
        top_holder_percentage = 0
        top5_percentage = 0
        if holders_info["success"] and holders_info["data"]:
            # 최대 홀더 비율
            if holders_info["data"]:
                top_holder_percentage = holders_info["data"][0]["percentage"]
                
                if top_holder_percentage > 50:  # 한 주소가 50% 이상 보유
                    scam_score += 20
                    scam_indicators.append(f"단일 주소가 토큰의 {top_holder_percentage:.2f}% 보유")
                elif top_holder_percentage > 30:  # 한 주소가 30% 이상 보유
                    scam_score += 10
                    scam_indicators.append(f"단일 주소가 토큰의 {top_holder_percentage:.2f}% 보유")
            
            # 상위 5개 홀더 집중도
            if len(holders_info["data"]) >= 5:
                top5_percentage = sum(holder["percentage"] for holder in holders_info["data"][:5])
                
                if top5_percentage > 90:  # 상위 5개 주소가 90% 이상 보유
                    scam_score += 15
                    scam_indicators.append(f"상위 5개 주소가 토큰의 {top5_percentage:.2f}% 보유")
                elif top5_percentage > 80:  # 상위 5개 주소가 80% 이상 보유
                    scam_score += 10
                    scam_indicators.append(f"상위 5개 주소가 토큰의 {top5_percentage:.2f}% 보유")
        else:
            scam_score += 5
            scam_indicators.append("홀더 정보 없음")
        
        # 5.4 거래 내역 분석
        if trades_info["success"] and trades_info["data"]:
            # 매수/매도 비율 분석
            buys = sum(1 for trade in trades_info["data"] if trade["type"] == "buy")
            sells = sum(1 for trade in trades_info["data"] if trade["type"] == "sell")
            total = len(trades_info["data"])
            
            if total > 0:
                sell_ratio = sells / total
                
                if sell_ratio > 0.8:  # 80% 이상이 매도 거래
                    scam_score += 15
                    scam_indicators.append("대부분이 매도 거래")
        else:
            scam_score += 5
            scam_indicators.append("거래 내역 정보 없음")
        
        # 5.5 토큰 생성 시간 분석
        days_since_creation = 0
        if token_info.get("created_at"):
            created_date = datetime.fromisoformat(token_info["created_at"].replace("Z", "+00:00"))
            days_since_creation = (datetime.now().astimezone() - created_date).days
            
            if days_since_creation < 7:  # 1주일 이내 생성된 토큰
                scam_score += 10
                scam_indicators.append(f"최근에 생성된 토큰 ({days_since_creation}일 전)")
        
        # 스캠 가능성 판단
        scam_risk = "낮음"
        if scam_score >= 70:
            scam_risk = "매우 높음"
        elif scam_score >= 50:
            scam_risk = "높음"
        elif scam_score >= 30:
            scam_risk = "중간"
        
        return {
            "success": True,
            "token_name": token_info["name"],
            "token_symbol": token_info["symbol"],
            "scam_score": scam_score,
            "scam_risk": scam_risk,
            "scam_indicators": scam_indicators,
            "analysis": {
                "liquidity": total_liquidity,
                "top_holder_percentage": top_holder_percentage,
                "top5_percentage": top5_percentage,
                "days_since_creation": days_since_creation
            }
        }
    
    except Exception as e:
        logger.error(f"스캠 토큰 분석 오류: {str(e)}")
        return {"success": False, "error": str(e)}

# 토큰 종합 분석 함수
async def get_token_comprehensive_analysis(token_address, network="ethereum"):
    try:
        # 1. 기본 토큰 정보
        token_info = await get_token_info(token_address, network)
        if not token_info["success"]:
            return token_info
        
        # 2. 유동성 풀 정보
        pools_info = await get_token_pools(token_address, network)
        if pools_info["success"] and pools_info["data"]:
            token_info["pools"] = pools_info["data"]
            token_info["total_liquidity"] = sum(pool["liquidity"] for pool in pools_info["data"])
            token_info["total_volume_24h"] = sum(pool["volume_24h"] for pool in pools_info["data"])
        
        # 3. 홀더 정보
        holders_info = await get_token_holders(token_address, network, limit=10)
        if holders_info["success"] and holders_info["data"]:
            token_info["top_holders"] = holders_info["data"]
            
            # 집중도 계산
            if len(holders_info["data"]) > 0:
                token_info["top_holder_percentage"] = holders_info["data"][0]["percentage"]
            
            if len(holders_info["data"]) >= 5:
                token_info["top5_concentration"] = sum(holder["percentage"] for holder in holders_info["data"][:5])
        
        # 4. 거래 내역 정보
        trades_info = await get_token_trades(token_address, network, limit=20)
        if trades_info["success"] and trades_info["data"]:
            token_info["recent_trades"] = trades_info["data"]
            
            # 매수/매도 비율 계산
            if trades_info["data"]:
                buys = sum(1 for trade in trades_info["data"] if trade["type"] == "buy")
                sells = sum(1 for trade in trades_info["data"] if trade["type"] == "sell")
                total = len(trades_info["data"])
                
                if total > 0:
                    token_info["buy_sell_ratio"] = buys / total
        
        # 5. 스캠 분석
        scam_check = await check_token_scam(token_address, network)
        if scam_check["success"]:
            token_info["scam_analysis"] = {
                "score": scam_check["scam_score"],
                "risk": scam_check["scam_risk"],
                "indicators": scam_check["scam_indicators"]
            }
        
        return token_info
    
    except Exception as e:
        logger.error(f"토큰 종합 분석 오류: {str(e)}")
        return {"success": False, "error": str(e)}