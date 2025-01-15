import os
import uuid
import base64
from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_bcrypt import Bcrypt
from dotenv import load_dotenv
import pymysql
from pytrends.request import TrendReq
import requests
import feedparser
from bs4 import BeautifulSoup
import re
from datetime import datetime
import urllib
import json
import pandas as pd
from sklearn.metrics.pairwise import cosine_similarity
import base64

app = Flask(__name__)
CORS(app)
bcrypt = Bcrypt(app)
load_dotenv()  # .env 파일에서 환경 변수 로드
# ----- DB 설정 -----
db_config = {
    "host": os.getenv("DB_HOST", "localhost"),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_NAME", "your_database"),
    "port": int(os.getenv("DB_PORT", 3306))
    
}

@app.route('/trending_searches', methods=['GET'])
def get_trending_searches():
    """
    Google Trends의 South Korea 인기 검색어를 반환
    """
    try:
        # Pytrends 설정
        pytrends = TrendReq(hl='ko-KR', tz=540)
        df = pytrends.trending_searches(pn='south_korea')  # South Korea의 인기 검색어

        # 데이터프레임을 리스트로 변환
        trending_searches = df[0].tolist()

        return jsonify({
            "success": True,
            "trending_searches": trending_searches
        }), 200

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

CLIENT_ID = 'VqHVz3Tfx4sHK9ebjLh0'
CLIENT_SECRET = 'S0nCIcmrWG'

# 네이버 뉴스 검색 API URL
API_URL = 'https://openapi.naver.com/v1/search/news.json'

@app.route('/search', methods=['GET'])
def search_news():
    # 쿼리 파라미터 받기
    query = request.args.get('query', '집')  # 기본값 '부동산'
    display = request.args.get('display', 10)  # 기본값 10
    start = request.args.get('start', 1)      # 기본값 1
    sort = request.args.get('sort', 'sim')    # 기본값 sim

    # 헤더 구성
    headers = {
        "X-Naver-Client-Id": CLIENT_ID,
        "X-Naver-Client-Secret": CLIENT_SECRET
    }

    # 요청 파라미터 구성
    params = {
        "query": query,
        "display": display,
        "start": start,
        "sort": sort
    }

    # 네이버 API 요청
    response = requests.get(API_URL, headers=headers, params=params)

    if response.status_code == 200:
        data = response.json()

        # 뉴스 제목과 링크 추출
        raw_items = [{"title": item["title"], "link": item["link"]} for item in data.get("items", [])]

        # 중복 제거: 제목을 기준으로
        seen_titles = set()
        unique_items = []
        for item in raw_items:
            if item["title"] not in seen_titles:
                unique_items.append(item)
                seen_titles.add(item["title"])

        return jsonify(unique_items)
    else:
        return jsonify({"error": "Failed to fetch data", "status_code": response.status_code}), response.status_code


##################################
# 1) 회원가입
##################################
@app.route('/add_user', methods=['POST'])
def add_user():
    """
    새로운 유저를 User 테이블에 추가
    - JSON 데이터로 처리
    """
    try:
        connection = pymysql.connect(**db_config)
        cursor = connection.cursor()

        # 요청 JSON 데이터 가져오기
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "No JSON data provided"}), 400

        username = data.get('username')
        password = data.get('password')
        name = data.get('name', '')

        if not username or not password:
            return jsonify({"success": False, "message": "아이디와 비밀번호는 필수입니다."}), 400

        # 비밀번호 해시
        hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')

        query = """
            INSERT INTO User (username, password, name)
            VALUES (%s, %s, %s)
        """
        cursor.execute(query, (username, hashed_password, name))
        connection.commit()

        return jsonify({"success": True, "message": "회원가입 성공!"}), 201

    except Exception as e:
        if 'connection' in locals():
            connection.rollback()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if 'connection' in locals():
            connection.close()

##################################
# 2) 로그인
##################################
@app.route("/login", methods=["POST"])
def login():
    """
    아이디(username) + 비밀번호로 로그인
    - JSON 데이터로 처리
    """
    try:
        connection = pymysql.connect(**db_config)
        cursor = connection.cursor()

        data = request.get_json()  # JSON 데이터 가져오기
        if not data:
            return jsonify({"success": False, "message": "No JSON data provided"}), 400

        username = data.get("username")
        password = data.get("password")

        if not username or not password:
            return jsonify({"success": False, "message": "아이디/비밀번호를 모두 입력해주세요."}), 400

        # DB에서 해당 user 가져오기
        query = "SELECT user_id, username, password, name FROM User WHERE username = %s"
        cursor.execute(query, (username,))
        result = cursor.fetchone()

        if not result:
            return jsonify({"success": False, "message": "존재하지 않는 사용자입니다."}), 404

        db_user_id, db_username, db_hashed_password, db_name = result

        # 비밀번호 검증
        if bcrypt.check_password_hash(db_hashed_password, password):
            return jsonify({
                "success": True,
                "message": "로그인 성공",
                "user_id": db_user_id,
                "username": db_username,
                "name": db_name
            }), 200
        else:
            return jsonify({"success": False, "message": "비밀번호가 틀립니다."}), 401

    except Exception as e:
        return jsonify({"success": False, "message": str(e), "user_id": db_user_id}), 500
    finally:
        if 'connection' in locals():
            connection.close()

##################################
# 3) 프로필 등록 (마이페이지 저장)
##################################
@app.route('/profile', methods=['PUT'])
def save_or_update_profile():
    """
    - user_id(필수) + 프로필 상세정보를 받아 UserProfile 테이블에 INSERT 또는 UPDATE
    - Base64로 인코딩된 이미지를 받아서 서버에 저장 -> photo_url 에 경로 저장
    """
    try:
        # 데이터베이스 연결 (DictCursor 사용)
        connection = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)
        cursor = connection.cursor()

        # 요청 데이터 가져오기
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "No JSON data provided"}), 400

        # 필수 데이터 확인
        user_id = data.get('user_id')
        if not user_id:
            return jsonify({"success": False, "message": "user_id is required"}), 400

        # 기타 필드 가져오기
        age = data.get('age', None)
        phone = data.get('phone', None)
        is_smoking_str = data.get('is_smoking', '0')
        snoring_str = data.get('snoring', '0')
        introduction = data.get('introduction', '')
        wishes = data.get('wishes', '')
        preferred_region = data.get('preferred_region', '')
        budget = data.get('budget', 0)
        profile_image_base64 = data.get('profile_image', None)

        # 흡연 여부, 코골이 여부 변환
        is_smoking = 1 if is_smoking_str == '1' else 0
        snoring = 1 if snoring_str == '1' else 0

        # Base64 이미지 처리
        photo_url = None
        if profile_image_base64:
            try:
                UPLOAD_FOLDER = 'uploads'
                if not os.path.exists(UPLOAD_FOLDER):
                    os.makedirs(UPLOAD_FOLDER)

                header, encoded = profile_image_base64.split(',', 1)
                file_extension = header.split('/')[1].split(';')[0]  # 예: jpeg, png 등
                filename = f"{uuid.uuid4()}.{file_extension}"
                save_path = os.path.join(UPLOAD_FOLDER, filename)

                with open(save_path, "wb") as f:
                    f.write(base64.b64decode(encoded))

                # DB에는 이미지 파일 경로를 저장
                photo_url = save_path
            except Exception as e:
                return jsonify({"success": False, "message": f"이미지 처리 중 오류 발생: {str(e)}"}), 400

        # 동일한 user_id가 있는지 확인
        check_query = "SELECT profile_id FROM UserProfile WHERE user_id = %s"
        cursor.execute(check_query, (user_id,))
        existing_profile = cursor.fetchone()

        if existing_profile:
            # 기존 데이터 업데이트
            update_sql = """
                UPDATE UserProfile
                SET age = %s,
                    phone = %s,
                    photo_url = %s,
                    is_smoking = %s,
                    snoring = %s,
                    introduction = %s,
                    wishes = %s,
                    preferred_region = %s,
                    budget = %s,
                    updated_at = NOW()
                WHERE user_id = %s
            """
            cursor.execute(update_sql, (
                age, phone, photo_url, is_smoking, snoring,
                introduction, wishes, preferred_region, budget, user_id
            ))
            connection.commit()

            return jsonify({
                "success": True,
                "message": "프로필 정보가 업데이트되었습니다.",
                "profile_id": existing_profile['profile_id']
            }), 200
        else:
            # 새 데이터 삽입
            insert_sql = """
                INSERT INTO UserProfile (
                    user_id,
                    age,
                    phone,
                    photo_url,
                    is_smoking,
                    snoring,
                    introduction,
                    wishes,
                    preferred_region,
                    budget
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            cursor.execute(insert_sql, (
                user_id, age, phone, photo_url, is_smoking, snoring,
                introduction, wishes, preferred_region, budget
            ))
            connection.commit()
            new_profile_id = cursor.lastrowid

            return jsonify({
                "success": True,
                "message": "새 프로필이 생성되었습니다.",
                "profile_id": new_profile_id
            }), 201

    except Exception as e:
        if 'connection' in locals():
            connection.rollback()
        return jsonify({"success": False, "message": str(e)}), 500

    finally:
        if 'connection' in locals():
            connection.close()



##################################
# 4) 특정 유저의 프로필 상세 + 리뷰 조회
##################################
@app.route('/profile_detail', methods=['POST'])
def get_profile_detail():
    """
    - POST Body(JSON): { "user_id": 15 }
    - user_id에 맞는 프로필 정보를 반환, photo_url을 Base64 인코딩으로 반환
    - 리뷰 정보도 추가 반환
    """
    try:
        connection = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)
        cursor = connection.cursor()

        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "No JSON data provided"}), 400

        user_id = data.get('user_id')
        if not user_id:
            return jsonify({"success": False, "message": "user_id is required"}), 400

        # 유저 + 프로필 조회
        query_profile = """
            SELECT 
                U.user_id,
                U.username,
                U.name,
                P.profile_id,
                P.age,
                P.phone,
                P.photo_url,
                P.is_smoking,
                P.snoring,
                P.introduction,
                P.wishes,
                P.preferred_region,
                P.budget,
                P.created_at,
                P.updated_at
            FROM UserProfile AS P
            JOIN User AS U
              ON P.user_id = U.user_id
            WHERE U.user_id = %s
        """
        cursor.execute(query_profile, (user_id,))
        profile_row = cursor.fetchone()

        if not profile_row:
            return jsonify({"success": False, "message": "해당 유저의 프로필이 존재하지 않습니다."}), 404

        # Base64로 변환
        if profile_row.get('photo_url'):
            try:
                with open(profile_row['photo_url'], 'rb') as image_file:
                    photo_base64 = base64.b64encode(image_file.read()).decode('utf-8')
                profile_row['photo_base64'] = photo_base64
            except FileNotFoundError:
                profile_row['photo_base64'] = None
        else:
            profile_row['photo_base64'] = None

        # 리뷰 조회
        query_reviews = """
            SELECT 
                R.review_id,
                R.content,
                R.rating,
                R.created_at
            FROM Review AS R
            WHERE R.reviewee_id = %s
        """
        cursor.execute(query_reviews, (user_id,))
        reviews = cursor.fetchall()

        return jsonify({
            "success": True,
            "profile": profile_row,
            "reviews": reviews
        }), 200

    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if 'connection' in locals():
            connection.close()


##################################
# 5) 팔로우 요청
##################################
@app.route('/follow', methods=['POST'])
def follow_user():
    """
    - JSON: { "follower_id": 1, "following_id": 2 }
    - follower_id가 following_id를 팔로우
    """
    try:
        connection = pymysql.connect(**db_config)
        cursor = connection.cursor()

        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "No JSON data provided"}), 400

        follower_id = data.get('follower_id')
        following_id = data.get('following_id')

        if not follower_id or not following_id:
            return jsonify({"success": False, "message": "follower_id와 following_id는 필수입니다."}), 400
        if follower_id == following_id:
            return jsonify({"success": False, "message": "자신을 팔로우할 수 없습니다."}), 400

        # 이미 팔로우 상태인지 확인
        check_sql = """
            SELECT follow_id FROM Follow
            WHERE follower_id = %s AND following_id = %s
        """
        cursor.execute(check_sql, (follower_id, following_id))
        existing = cursor.fetchone()
        if existing:
            return jsonify({"success": False, "message": "이미 팔로우 상태입니다."}), 400

        # 팔로우 추가
        insert_sql = """
            INSERT INTO Follow (follower_id, following_id)
            VALUES (%s, %s)
        """
        cursor.execute(insert_sql, (follower_id, following_id))
        connection.commit()

        return jsonify({"success": True, "message": "팔로우 완료"}), 201

    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if 'connection' in locals():
            connection.close()


##################################
# 6) 언팔로우 요청
##################################
@app.route('/unfollow', methods=['POST'])
def unfollow_user():
    """
    - JSON: { "follower_id": 1, "following_id": 2 }
    - follower_id가 following_id를 언팔로우
    """
    try:
        connection = pymysql.connect(**db_config)
        cursor = connection.cursor()

        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "No JSON data provided"}), 400

        follower_id = data.get('follower_id')
        following_id = data.get('following_id')

        if not follower_id or not following_id:
            return jsonify({"success": False, "message": "follower_id와 following_id는 필수입니다."}), 400
        if follower_id == following_id:
            return jsonify({"success": False, "message": "자신을 언팔로우할 수 없습니다."}), 400

        # 팔로우 삭제
        delete_sql = """
            DELETE FROM Follow
            WHERE follower_id = %s AND following_id = %s
        """
        cursor.execute(delete_sql, (follower_id, following_id))
        connection.commit()

        return jsonify({"success": True, "message": "언팔로우 완료"}), 200

    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if 'connection' in locals():
            connection.close()


##################################
# 7) 리뷰 작성
##################################
@app.route('/review', methods=['POST'])
def create_review():
    """
    - JSON: { "reviewer_id": 1, "reviewee_id": 2, "rating": 5, "content": "좋은 룸메이트!" }
    - reviewer_id가 reviewee_id에게 리뷰 작성
    - 조건: 서로 팔로우 관계인지 확인 후 INSERT
    """
    try:
        connection = pymysql.connect(**db_config)
        cursor = connection.cursor()

        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "No JSON data provided"}), 400

        reviewer_id = data.get('reviewer_id')
        reviewee_id = data.get('reviewee_id')
        rating = data.get('rating')
        content = data.get('content', '')

        if not reviewer_id or not reviewee_id or not rating:
            return jsonify({"success": False, "message": "reviewer_id, reviewee_id, rating은 필수입니다."}), 400
        if not (1 <= int(rating) <= 5):
            return jsonify({"success": False, "message": "평점은 1~5 사이의 값이어야 합니다."}), 400
        if reviewer_id == reviewee_id:
            return jsonify({"success": False, "message": "자신에게 리뷰를 작성할 수 없습니다."}), 400

        # 팔로우 관계 확인 (서로 맞팔 상태 확인)
        check_follow_sql = """
            SELECT 1 FROM Follow f1
            JOIN Follow f2
            ON f1.follower_id = f2.following_id AND f1.following_id = f2.follower_id
            WHERE f1.follower_id = %s AND f1.following_id = %s
        """
        cursor.execute(check_follow_sql, (reviewer_id, reviewee_id))
        follow_status = cursor.fetchone()

        if not follow_status:
            return jsonify({"success": False, "message": "서로 팔로우 상태가 아닙니다."}), 403

        # 리뷰 추가
        insert_sql = """
            INSERT INTO Review (reviewer_id, reviewee_id, rating, content)
            VALUES (%s, %s, %s, %s)
        """
        cursor.execute(insert_sql, (reviewer_id, reviewee_id, rating, content))
        connection.commit()

        return jsonify({"success": True, "message": "리뷰 작성 완료"}), 201

    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if 'connection' in locals():
            connection.close()

##################################
# 8) 상단 6개 리뷰 반환
##################################
@app.route('/reviews', methods=['GET'])
def get_top_reviews():
    """
    - 최신 리뷰 상위 6개 반환
    """
    try:
        connection = pymysql.connect(**db_config)
        cursor = connection.cursor(pymysql.cursors.DictCursor)

        # 최신 리뷰 상위 6개를 가져오는 쿼리
        query = """
            SELECT 
                R.review_id,
                R.reviewer_id,
                R.reviewee_id,
                R.rating,
                R.content,
                R.created_at,
                reviewer.username AS reviewer_username,
                reviewee.username AS reviewee_username
            FROM Review AS R
            JOIN User AS reviewer ON R.reviewer_id = reviewer.user_id
            JOIN User AS reviewee ON R.reviewee_id = reviewee.user_id
            ORDER BY R.created_at DESC
            LIMIT 6
        """
        cursor.execute(query)
        reviews = cursor.fetchall()  # 최신 리뷰 상위 6개

        return jsonify({
            "success": True,
            "reviews": reviews
        }), 200

    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if 'connection' in locals():
            connection.close()

@app.route('/get-coordinates', methods=['POST'])
def get_coordinates():
    try:
        # 요청 데이터에서 주소 추출
        data = request.get_json()
        address = data.get('address')

        if not address:
            return jsonify({'error': '주소를 제공해주세요.'}), 400

        # MySQL 데이터베이스 연결
        connection = pymysql.connect(**db_config)
        cursor = connection.cursor(pymysql.cursors.DictCursor)
            # 주소로 위도와 경도 조회
        query = """
                SELECT latitude, longitude
                FROM AddressInfo
                WHERE address = %s
            """
        cursor.execute(query, (address,))
        result = cursor.fetchone()

        connection.close()

        if result:
            return jsonify({
                'address': address,
                'latitude': result['latitude'],
                'longitude': result['longitude']
            }), 200
        else:
            return jsonify({'error': '주소를 찾을 수 없습니다.'}), 404

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/all_users', methods=['GET'])
def get_all_users():
    try:
        # 데이터베이스 연결 (DictCursor 설정)
        connection = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)
        cursor = connection.cursor()

        # 쿼리 실행 (profile_id 제외)
        query = """
            SELECT 
                user_id,
                age,
                phone,
                photo_url,
                is_smoking,
                snoring,
                introduction,
                wishes,
                preferred_region,
                budget,
                created_at,
                updated_at
            FROM UserProfile;
        """
        cursor.execute(query)
        users = cursor.fetchall()  # DictCursor로 딕셔너리 형식으로 결과 반환

        # photo_url 값을 Base64로 변환
        for user in users:
            photo_path = user.get('photo_url')  # DB에서 가져온 photo_url
            if photo_path and os.path.exists(photo_path):
                try:
                    with open(photo_path, 'rb') as img_file:
                        # Base64 인코딩
                        user['photo_base64'] = base64.b64encode(img_file.read()).decode('utf-8')
                except Exception as e:
                    user['photo_base64'] = None  # 에러 발생 시 None으로 처리
            else:
                user['photo_base64'] = None  # 경로가 없거나 파일이 없는 경우

        # JSON 응답 반환
        return jsonify({
            "success": True,
            "users": users  # Base64로 변환된 데이터를 포함한 결과 반환
        }), 200

    except Exception as e:
        # 예외 처리
        return jsonify({
            "success": False,
            "message": str(e)
        }), 500

    finally:
        # 연결 닫기
        if 'connection' in locals():
            connection.close()
            
@app.route('/user_name', methods=['GET'])
def user_name():
    """
    User 테이블에서 user_id와 name만 반환하는 엔드포인트
    """
    try:
        # 데이터베이스 연결
        connection = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)
        cursor = connection.cursor()

        # 쿼리 실행 (user_id와 name만 선택)
        query = """
            SELECT 
                user_id,
                name
            FROM User;
        """
        cursor.execute(query)
        users = cursor.fetchall()  # DictCursor로 딕셔너리 형식으로 결과 가져오기

        # JSON 응답 반환
        return jsonify({
            "success": True,
            "users": users
        }), 200

    except Exception as e:
        # 예외 처리
        return jsonify({
            "success": False,
            "message": str(e)
        }), 500

    finally:
        # 연결 닫기
        if 'connection' in locals():
            connection.close()

@app.route('/following', methods=['POST'])
def get_following():
    """
    follower_id를 받아 해당 유저가 팔로우하고 있는 following_id 리스트를 반환
    """
    try:
        # 요청에서 JSON 데이터 가져오기
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "No JSON data provided"}), 400

        follower_id = data.get('follower_id')  # follower_id 추출
        if not follower_id:
            return jsonify({"success": False, "message": "follower_id is required"}), 400

        # 데이터베이스 연결
        connection = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)
        cursor = connection.cursor()

        # 쿼리 실행 (follower_id가 following하고 있는 모든 following_id 조회)
        query = """
            SELECT following_id 
            FROM Follow
            WHERE follower_id = %s;
        """
        cursor.execute(query, (follower_id,))
        results = cursor.fetchall()  # 결과 가져오기

        # following_id 리스트로 변환
        following_ids = [row['following_id'] for row in results]

        # JSON 응답 반환
        return jsonify({
            "success": True,
            "follower_id": follower_id,
            "following_ids": following_ids
        }), 200

    except Exception as e:
        # 예외 처리
        return jsonify({"success": False, "message": str(e)}), 500

    finally:
        # 연결 닫기
        if 'connection' in locals():
            connection.close()

@app.route('/recommend_roommates', methods=['POST'])
def recommend_roommates():
    """
    user_id를 입력받아 유사한 프로필의 사용자 추천
    """
    try:
        # 데이터베이스 연결
        connection = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)
        cursor = connection.cursor()

        # 요청 JSON 데이터 가져오기
        data = request.get_json()
        user_id = data.get('user_id')

        if not user_id:
            return jsonify({"success": False, "message": "user_id is required"}), 400

        # 대상 사용자 프로필 가져오기
        query_user = "SELECT * FROM UserProfile WHERE user_id = %s"
        cursor.execute(query_user, (user_id,))
        user_profile = cursor.fetchone()

        if not user_profile:
            return jsonify({"success": False, "message": "사용자 프로필을 찾을 수 없습니다."}), 404

        # 모든 프로필 가져오기
        query_all = "SELECT * FROM UserProfile WHERE user_id != %s"
        cursor.execute(query_all, (user_id,))
        all_profiles = cursor.fetchall()

        # 데이터 준비 및 유사도 계산
        user_df = pd.DataFrame([user_profile])
        profiles_df = pd.DataFrame(all_profiles)

        features = ['age', 'is_smoking', 'snoring', 'budget']
        user_vector = user_df[features].values
        profiles_vectors = profiles_df[features].values

        # 코사인 유사도 계산
        similarities = cosine_similarity(user_vector, profiles_vectors).flatten()
        profiles_df['similarity'] = similarities

        # 유사도가 높은 순으로 정렬
        recommendations = profiles_df.sort_values(by='similarity', ascending=False).head(5).to_dict(orient='records')

        # photo_url을 Base64로 변환
        for recommendation in recommendations:
            photo_path = recommendation.get('photo_url')
            if photo_path and os.path.exists(photo_path):
                try:
                    with open(photo_path, 'rb') as img_file:
                        # Base64로 인코딩
                        recommendation['photo_base64'] = base64.b64encode(img_file.read()).decode('utf-8')
                except Exception as e:
                    recommendation['photo_base64'] = None  # 에러 발생 시 None으로 설정
            else:
                recommendation['photo_base64'] = None  # 경로가 없거나 파일이 없는 경우

        return jsonify({
            "success": True,
            "recommendations": recommendations
        }), 200

    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

    finally:
        if 'connection' in locals():
            connection.close()
if __name__ == '__main__':
    app.run(host='0.0.0.0', debug=True, port=5002)
