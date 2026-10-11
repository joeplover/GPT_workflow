"""Local administrator provisioning; public registration never claims old projects."""

import argparse
from getpass import getpass
import re
import uuid

from dotenv import load_dotenv
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import get_engine, initialize_database
from .models import Project, ProjectEvent, User
from .security import hash_password


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="初始化系统管理员（密码交互输入，不写入命令历史）"
    )
    parser.add_argument("username")
    parser.add_argument(
        "--claim-legacy", action="store_true", help="将无归属的历史项目分配给此管理员"
    )
    args = parser.parse_args()
    username = args.username.lower()
    if not re.fullmatch(r"[a-z0-9_\-.]{3,64}", username):
        parser.error("用户名格式不正确")
    password = getpass("管理员密码（至少10位）: ")
    if len(password) < 10 or password != getpass("再次输入密码: "):
        parser.error("密码长度不足或两次不一致")
    initialize_database()
    with Session(get_engine()) as session, session.begin():
        if session.scalar(select(User).where(User.username == username)):
            parser.error("用户已经存在；本命令不会覆盖现有账号")
        user = User(
            id=uuid.uuid4().hex,
            username=username,
            display_name="系统管理员",
            password_hash=hash_password(password),
            is_admin=True,
        )
        session.add(user)
        session.flush()
        claimed = 0
        if args.claim_legacy:
            for project in session.scalars(
                select(Project)
                .where(Project.owner_id.is_(None), Project.team_id.is_(None))
                .with_for_update()
            ):
                project.owner_id = user.id
                session.add(
                    ProjectEvent(
                        project_id=project.id,
                        actor_id=user.id,
                        operation="project.legacy.claim",
                        changes=[
                            {"path": ["owner_id"], "before": None, "after": user.id}
                        ],
                    )
                )
                claimed += 1
    print(f"管理员已创建；分配历史项目 {claimed} 个。请访问 /login 登录。")


if __name__ == "__main__":
    main()
