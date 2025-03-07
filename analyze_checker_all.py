import logging
import asyncio
import sqlite3
from datetime import datetime, timedelta
import requests
import json
from typing import Dict, List, Any, Tuple
from scam_checker_all import check_token_scam
import time

# 지원하는 네트워크 목록
SUPPORTED_NETWORKS = {
    "ethereum": "이더리움 (ETH)",
    "bsc": "바이낸스 스마트 체인 (BSC)",
    "polygon": "폴리곤 (MATIC)",
    "arbitrum": "아비트럼 (ARB)",
    "avalanche": "아발란체 (AVAX)",
    "optimism": "옵티미즘 (OP)",
    "base": "베이스 (BASE)",
    "solana": "솔라나 (SOL)"
}

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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

# 특정 사용자의 토큰 목록 가져오기
def get_user_tokens(user_id: int) -> List[Tuple[str, str]]:
    """
    특정 사용자의 토큰 목록을 가져옵니다.
    
    Args:
        user_id (int): 사용자 ID
        
    Returns:
        List[Tuple[str, str]]: (token_address, network) 형태의 튜플 리스트
    """
    conn = sqlite3.connect('tokens.db')
    cursor = conn.cursor()
    cursor.execute("SELECT token, network FROM tokens WHERE user_id = ?", (user_id,))
    tokens = cursor.fetchall()
    conn.close()
    return tokens

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

# 토큰 가격 정보 조회 개선
async def get_token_price(token_address: str, network: str = "ethereum") -> Dict[str, Any]:
    """
    토큰의 가격 정보를 조회합니다.
    
    Args:
        token_address (str): 토큰 주소
        network (str, optional): 네트워크 이름. 기본값은 "ethereum"
        
    Returns:
        Dict[str, Any]: 가격 정보
    """
    try:
        # 네트워크 ID 매핑
        network_mapping = {
            "ethereum": "eth",
            "bsc": "bsc",
            "polygon": "polygon_pos",
            "arbitrum": "arbitrum",
            "solana": "solana",
            "avalanche": "avax",
            "optimism": "optimism",
            "base": "base"
        }
        
        # 네트워크 ID 변환
        api_network = network_mapping.get(network.lower(), network.lower())
        
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
                
                # 더 많은 정보 추출
                price_usd = float(attrs.get('price_usd') or 0)
                token_name = attrs.get('name', '알 수 없음')
                token_symbol = attrs.get('symbol', '???')
                decimals = int(attrs.get('decimals') or 0)
                total_supply = float(attrs.get('total_supply') or 0)
                
                return {
                    "success": True,
                    "price": price_usd,
                    "name": token_name,
                    "symbol": token_symbol,
                    "decimals": decimals,
                    "total_supply": total_supply,
                    "coingecko_id": attrs.get('coingecko_coin_id')
                }
            else:
                logger.error(f"API 응답에 필요한 데이터가 없습니다: {data}")
                return {"success": False, "error": "API 응답에 필요한 데이터가 없습니다"}
        else:
            logger.error(f"API 응답 오류: 상태 코드 {response.status_code}, 응답: {response.text}")
            
            # 404 에러 메시지 개선
            if response.status_code == 404:
                return {"success": False, "error": f"토큰을 찾을 수 없습니다. 주소가 올바른지, 네트워크가 맞는지 확인하세요."}
            
            return {"success": False, "error": f"토큰 정보를 찾을 수 없습니다. 상태 코드: {response.status_code}"}
    
    except Exception as e:
        logger.error(f"가격 조회 오류: {str(e)}")
        return {"success": False, "error": str(e)}

# 토큰 추가 정보 조회 개선
async def get_token_additional_info(token_address: str, network: str = "ethereum") -> Dict[str, Any]:
    """
    토큰의 추가 정보를 조회합니다.
    
    Args:
        token_address (str): 토큰 주소
        network (str, optional): 네트워크 이름. 기본값은 "ethereum"
        
    Returns:
        Dict[str, Any]: 추가 정보
    """
    try:
        # 네트워크 ID 매핑
        network_mapping = {
            "ethereum": "eth",
            "bsc": "bsc",
            "polygon": "polygon_pos",
            "arbitrum": "arbitrum",
            "solana": "solana",
            "avalanche": "avax",
            "optimism": "optimism",
            "base": "base"
        }
        
        # 네트워크 ID 변환
        api_network = network_mapping.get(network.lower(), network.lower())
        
        # 결과 초기화
        result = {"success": True}
        
        # 1. 토큰 정보 엔드포인트 (소셜 미디어, 웹사이트 등)
        info_url = f"https://api.geckoterminal.com/api/v2/networks/{api_network}/tokens/{token_address}/info"
        headers = {"Accept": "application/json"}
        
        logger.info(f"토큰 정보 API 요청: {info_url}")
        info_response = await rate_limited_request(info_url, headers=headers)
        
        if info_response.status_code == 200:
            info_data = info_response.json()
            if 'data' in info_data and 'attributes' in info_data['data']:
                attrs = info_data['data']['attributes']
                
                # 소셜 미디어 및 웹사이트 정보
                result["image_url"] = attrs.get('image_url')
                result["websites"] = attrs.get('websites', [])
                result["description"] = attrs.get('description')
                result["discord_url"] = attrs.get('discord_url')
                result["telegram_handle"] = attrs.get('telegram_handle')
                result["twitter_handle"] = attrs.get('twitter_handle')
                result["categories"] = attrs.get('categories', [])
                result["gt_score"] = float(attrs.get('gt_score') or 0)
        
        # 2. 토큰 기본 정보 및 시장 데이터
        token_url = f"https://api.geckoterminal.com/api/v2/networks/{api_network}/tokens/{token_address}"
        token_response = await rate_limited_request(token_url, headers=headers)
        
        if token_response.status_code == 200:
            token_data = token_response.json()
            if 'data' in token_data and 'attributes' in token_data['data']:
                attrs = token_data['data']['attributes']
                
                # 시가총액
                if 'fdv_usd' in attrs and attrs['fdv_usd']:
                    result["market_cap"] = float(attrs['fdv_usd'])
                
                # 총 공급량
                if 'total_supply' in attrs and attrs['total_supply']:
                    result["total_supply"] = float(attrs['total_supply'])
                
                # 가격 변동
                if 'price_change_percentage' in attrs and attrs['price_change_percentage']:
                    price_changes = {}
                    for period, value in attrs['price_change_percentage'].items():
                        if value:
                            price_changes[period] = float(value)
                    result["price_changes"] = price_changes
        
        # 3. 풀 정보 조회
        pools_url = f"https://api.geckoterminal.com/api/v2/networks/{api_network}/tokens/{token_address}/pools"
        pools_response = await rate_limited_request(pools_url, headers=headers)
        
        if pools_response.status_code == 200:
            pools_data = pools_response.json()
            
            if 'data' in pools_data and pools_data['data']:
                # 총 유동성 계산
                total_liquidity = 0
                total_volume = 0
                top_dex = None
                top_liquidity = 0
                pools_info = []
                
                for pool in pools_data['data']:
                    if 'attributes' in pool:
                        pool_attr = pool['attributes']
                        
                        # 풀 정보 저장
                        pool_info = {
                            "address": pool_attr.get('address'),
                            "name": pool_attr.get('name'),
                            "dex_name": pool_attr.get('dex_name'),
                            "created_at": pool_attr.get('pool_created_at')
                        }
                        
                        # 유동성 합산
                        if 'reserve_in_usd' in pool_attr and pool_attr['reserve_in_usd']:
                            pool_liquidity = float(pool_attr['reserve_in_usd'])
                            total_liquidity += pool_liquidity
                            pool_info["liquidity"] = pool_liquidity
                            
                            # 가장 큰 유동성을 가진 DEX 찾기
                            if pool_liquidity > top_liquidity:
                                top_liquidity = pool_liquidity
                                top_dex = pool_attr.get('dex_name')
                        
                        # 거래량 합산
                        if 'volume_usd' in pool_attr and pool_attr['volume_usd']:
                            volume_data = {}
                            for period, value in pool_attr['volume_usd'].items():
                                if value:
                                    volume_data[period] = float(value)
                                    if period == 'h24' and value:
                                        total_volume += float(value)
                            
                            pool_info["volume"] = volume_data
                        
                        # 거래 건수
                        if 'transactions' in pool_attr and pool_attr['transactions']:
                            pool_info["transactions"] = pool_attr['transactions']
                        
                        pools_info.append(pool_info)
                
                result["liquidity"] = total_liquidity
                result["volume_24h"] = total_volume
                result["top_dex"] = top_dex
                result["pools"] = pools_info
        
        # 4. 가격 차트 데이터 (OHLCV)
        ohlcv_url = f"https://api.geckoterminal.com/api/v2/networks/{api_network}/tokens/{token_address}/ohlcv/day"
        ohlcv_response = await rate_limited_request(ohlcv_url, headers=headers)
        
        if ohlcv_response.status_code == 200:
            ohlcv_data = ohlcv_response.json()
            if 'data' in ohlcv_data and 'attributes' in ohlcv_data['data'] and 'ohlcv_list' in ohlcv_data['data']['attributes']:
                result["ohlcv_data"] = ohlcv_data['data']['attributes']['ohlcv_list']
        
        return result
    
    except Exception as e:
        logger.error(f"추가 정보 조회 오류: {str(e)}")
        return {"success": False, "error": str(e)}

# 토큰 종합 분석 개선
async def analyze_token(token_address: str, network: str = "ethereum") -> Dict[str, Any]:
    """
    토큰을 분석하여 다양한 지표를 반환합니다.
    
    Args:
        token_address (str): 토큰 주소
        network (str, optional): 네트워크 이름. 기본값은 "ethereum"
        
    Returns:
        Dict[str, Any]: 분석 결과
    """
    try:
        # 네트워크 ID 변환
        network_mapping = {
            "ethereum": "eth",
            "bsc": "bsc",
            "polygon": "polygon_pos",
            "arbitrum": "arbitrum",
            "solana": "solana",
            "avalanche": "avax",
            "optimism": "optimism",
            "base": "base"
        }
        
        api_network = network_mapping.get(network.lower(), network.lower())
        
        # API 엔드포인트 구성
        url = f"https://api.geckoterminal.com/api/v2/networks/{api_network}/tokens/{token_address}"
        headers = {"Accept": "application/json"}
        
        logger.info(f"API 요청: {url}")
        
        # 일반 requests.get 대신 rate_limited_request 사용
        response = await rate_limited_request(url, headers=headers)
        
        # 초기화: token_info 변수를 여기서 정의
        token_info = {
            "name": "알 수 없음",
            "symbol": "???",
            "price": 0,
            "decimals": 0,
            "total_supply": 0,
            "market_cap": 0,
            "created_at": None
        }
        
        if response.status_code == 200:
            data = response.json()
            if 'data' in data and 'attributes' in data['data']:
                attrs = data['data']['attributes']
                
                # 기본 정보 추출
                token_info = {
                    "name": attrs.get('name', '알 수 없음'),
                    "symbol": attrs.get('symbol', '???'),
                    "price": float(attrs.get('price_usd') or 0),
                    "decimals": int(attrs.get('decimals') or 0),
                    "total_supply": float(attrs.get('total_supply') or 0),
                    "market_cap": float(attrs.get('market_cap_usd') or 0),
                    "created_at": attrs.get('created_at')
                }
                
                # 더 많은 정보 추출
                price_usd = float(attrs.get('price_usd') or 0)
                token_name = attrs.get('name', '알 수 없음')
                token_symbol = attrs.get('symbol', '???')
                decimals = int(attrs.get('decimals') or 0)
                total_supply = float(attrs.get('total_supply') or 0)
                
                return {
                    "success": True,
                    "token_address": token_address,
                    "network": network,
                    "name": token_name,
                    "symbol": token_symbol,
                    "price": price_usd,
                    "decimals": decimals,
                    "total_supply": total_supply,
                    "coingecko_id": attrs.get('coingecko_coin_id')
                }
            else:
                logger.error(f"API 응답에 필요한 데이터가 없습니다: {data}")
                return {"success": False, "error": "API 응답에 필요한 데이터가 없습니다"}
        else:
            logger.error(f"API 응답 오류: 상태 코드 {response.status_code}, 응답: {response.text}")
            
            # 404 에러 메시지 개선
            if response.status_code == 404:
                return {"success": False, "error": f"토큰을 찾을 수 없습니다. 주소가 올바른지, 네트워크가 맞는지 확인하세요."}
            
            return {"success": False, "error": f"토큰 정보를 찾을 수 없습니다. 상태 코드: {response.status_code}"}
    
    except Exception as e:
        logger.error(f"토큰 분석 오류: {str(e)}")
        return {"success": False, "error": str(e)}

# 모든 토큰 종합 분석 개선
async def analyze_all_tokens() -> Dict[str, Any]:
    """
    모든 토큰의 종합 분석을 수행합니다.
    
    Returns:
        Dict[str, Any]: 분석 결과
    """
    tokens = get_all_tokens()
    
    if not tokens:
        return {
            "success": False,
            "error": "추적 중인 토큰이 없습니다."
        }
    
    # 토큰 주소별로 중복 제거 (네트워크 고려)
    unique_tokens = {}
    token_users = {}  # 각 토큰을 추적하는 사용자 목록
    
    for user_id, token_address, network in tokens:
        key = f"{token_address.lower()}_{network.lower()}"
        if key not in unique_tokens:
            unique_tokens[key] = (token_address, network)
        
        # 토큰별 사용자 목록 추가
        if key not in token_users:
            token_users[key] = []
        token_users[key].append(user_id)
    
    results = []
    high_risk_count = 0
    network_distribution = {}  # 네트워크별 토큰 분포
    
    for key, (token_address, network) in unique_tokens.items():
        try:
            # 각 토큰 분석 사이에 지연 시간 추가
            await asyncio.sleep(2)  # 1초에서 2초로 증가
            
            analysis = await analyze_token(token_address, network)
            
            if analysis["success"]:
                # 고위험 토큰 카운트
                if "scam_analysis" in analysis and analysis["scam_analysis"]["risk"] in ["높음", "매우 높음"]:
                    high_risk_count += 1
                
                # 네트워크별 분포 계산
                if network not in network_distribution:
                    network_distribution[network] = 0
                network_distribution[network] += 1
                
                # 사용자 목록 추가
                analysis["users"] = token_users.get(key, [])
                analysis["user_count"] = len(token_users.get(key, []))
                
                # 포트폴리오 가치 계산 (실제 보유량 정보가 없으므로 예시)
                if "price" in analysis:
                    # 예시: 각 토큰당 1개씩 보유한다고 가정
                    token_value = analysis["price"]
                    total_portfolio_value += token_value
                
                results.append(analysis)
            else:
                results.append({
                    "success": False,
                    "token_address": token_address,
                    "network": network,
                    "error": analysis["error"],
                    "users": token_users.get(key, []),
                    "user_count": len(token_users.get(key, []))
                })
        except Exception as e:
            logger.error(f"토큰 {token_address} 분석 중 오류: {str(e)}")
            results.append({
                "success": False,
                "token_address": token_address,
                "network": network,
                "error": str(e),
                "users": token_users.get(key, []),
                "user_count": len(token_users.get(key, []))
            })
    
    # 인기 토큰 순위 (사용자 수 기준)
    popular_tokens = sorted(
        [r for r in results if r["success"]],
        key=lambda x: x["user_count"],
        reverse=True
    )[:10]
    
    # 고위험 토큰 목록
    risky_tokens = [
        r for r in results 
        if r["success"] and "scam_analysis" in r and r["scam_analysis"]["risk"] in ["높음", "매우 높음"]
    ]
    
    # 최근 추가된 토큰 (풀 생성 시간 기준)
    recent_tokens = sorted(
        [r for r in results if r["success"] and "days_since_creation" in r],
        key=lambda x: x["days_since_creation"]
    )[:10]
    
    return {
        "success": True,
        "timestamp": datetime.now().isoformat(),
        "total_tokens": len(unique_tokens),
        "high_risk_count": high_risk_count,
        "network_distribution": network_distribution,
        "popular_tokens": popular_tokens,
        "risky_tokens": risky_tokens,
        "recent_tokens": recent_tokens,
        "results": results
    }

# 특정 사용자의 모든 토큰 종합 분석 개선
async def analyze_user_tokens(user_id: int) -> Dict[str, Any]:
    """
    특정 사용자의 모든 토큰 종합 분석을 수행합니다.
    
    Args:
        user_id (int): 사용자 ID
        
    Returns:
        Dict[str, Any]: 분석 결과
    """
    tokens = get_user_tokens(user_id)
    
    if not tokens:
        return {
            "success": False,
            "error": "추적 중인 토큰이 없습니다."
        }
    
    results = []
    high_risk_count = 0
    total_portfolio_value = 0
    portfolio_risk_score = 0
    
    for token_address, network in tokens:
        try:
            # 각 토큰 분석 사이에 지연 시간 추가
            await asyncio.sleep(2)  # 1초에서 2초로 증가
            
            analysis = await analyze_token(token_address, network)
            
            if analysis["success"]:
                # 고위험 토큰 카운트
                if "scam_analysis" in analysis and analysis["scam_analysis"]["risk"] in ["높음", "매우 높음"]:
                    high_risk_count += 1
                    portfolio_risk_score += analysis["scam_analysis"]["score"]
                
                # 포트폴리오 가치 계산 (실제 보유량 정보가 없으므로 예시)
                if "price" in analysis:
                    # 예시: 각 토큰당 1개씩 보유한다고 가정
                    token_value = analysis["price"]
                    total_portfolio_value += token_value
                
                results.append(analysis)
            else:
                results.append({
                    "success": False,
                    "token_address": token_address,
                    "network": network,
                    "error": analysis["error"]
                })
        except Exception as e:
            logger.error(f"토큰 {token_address} 분석 중 오류: {str(e)}")
            results.append({
                "success": False,
                "token_address": token_address,
                "network": network,
                "error": str(e)
            })
    
    # 포트폴리오 평균 위험도 계산
    avg_portfolio_risk = portfolio_risk_score / len(tokens) if tokens else 0
    
    # 포트폴리오 다양성 점수 (네트워크 및 토큰 유형 다양성)
    networks = set(network for _, network in tokens)
    network_diversity = len(networks) / len(SUPPORTED_NETWORKS) if tokens else 0
    
    # 토큰 유형 다양성 (카테고리 기반)
    categories = set()
    for result in results:
        if result.get("success") and "categories" in result:
            categories.update(result["categories"])
    
    category_diversity = min(1.0, len(categories) / 5) if categories else 0
    
    # 포트폴리오 다양성 종합 점수 (0-100)
    diversity_score = (network_diversity * 50 + category_diversity * 50)
    
    return {
        "success": True,
        "user_id": user_id,
        "timestamp": datetime.now().isoformat(),
        "total_tokens": len(tokens),
        "high_risk_count": high_risk_count,
        "portfolio_value": total_portfolio_value,
        "portfolio_risk": avg_portfolio_risk,
        "portfolio_diversity": {
            "score": diversity_score,
            "networks": list(networks),
            "categories": list(categories)
        },
        "results": results
    }

# 메인 함수 (테스트용)
async def main():
    # 특정 사용자의 토큰 분석 테스트
    user_id = 123456789  # 테스트용 사용자 ID
    result = await analyze_user_tokens(user_id)
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    asyncio.run(main()) 