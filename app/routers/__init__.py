"""app.routers - 기존 app/main.py의 인라인 엔드포인트와 별개로, 애드온성 라우터를 담는 패키지.

지금은 monitoring_addon.py 하나뿐입니다. 기존 REST API(설정/실행/대시보드 관련)는 앞으로도
계속 app/main.py에 그대로 두고, 이 패키지는 "기존 코드를 건드리지 않고 얹는" 확장 기능
전용입니다.
"""
