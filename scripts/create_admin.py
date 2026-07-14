"""웹 화면 없이 바로 승인된 관리자 계정을 만드는 비상용 CLI.

보통은 회원가입 화면(/signup)에서 서비스 최초 가입자가 자동으로 관리자가 되므로 이
스크립트가 필요 없습니다. 승인해줄 관리자가 아예 없어졌거나(계정 삭제 실수 등) 브라우저
접근 없이 서버에서 직접 계정을 만들어야 하는 비상 상황에만 쓰세요.

실행: python scripts/create_admin.py <아이디>
"""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qa_agent.users import UserStore  # noqa: E402


def main() -> None:
    if len(sys.argv) != 2:
        print("사용법: python scripts/create_admin.py <아이디>")
        return
    username = sys.argv[1]

    password = getpass.getpass("설정할 비밀번호를 입력하세요: ")
    if not password:
        print("비밀번호가 비어 있습니다.")
        return
    confirm = getpass.getpass("확인을 위해 한 번 더 입력하세요: ")
    if password != confirm:
        print("입력한 두 값이 서로 다릅니다.")
        return

    store = UserStore(path=str(Path("data") / "users.db"))
    user = store.create_user(username, password)
    if not user:
        print(f"'{username}' 아이디가 이미 사용 중입니다.")
        return
    if user["status"] != "approved" or user["role"] != "admin":
        # 이미 다른 계정이 있는 상태에서 실행하면 일반 대기 계정으로 생성되므로, 곧바로 승인+관리자로 승격
        store.approve_user(username)
        store.set_role(username, "admin")
    print(f"관리자 계정 '{username}'을(를) 만들었습니다.")


if __name__ == "__main__":
    main()
