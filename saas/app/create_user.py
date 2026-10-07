"""Foydalanuvchi yaratish (birinchi adminni ham shu bilan):
   python -m app.create_user email@example.com PAROL [--admin]
"""
import sys

from .db import Base, SessionLocal, engine
from .models import Account, User
from .security import hash_password


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 2 or len(args[1]) < 8:
        sys.exit("Foydalanish: python -m app.create_user email PAROL(kamida 8 belgi) [--admin]")
    email, password = args[0].strip().lower(), args[1]
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        user = db.query(User).filter_by(email=email).first()
        if user:  # mavjud bo'lsa: parolni yangilaydi, --admin bo'lsa admin qiladi
            user.password_hash = hash_password(password)
            user.is_admin = user.is_admin or "--admin" in argv
        else:
            user = User(email=email, password_hash=hash_password(password), is_admin="--admin" in argv)
            user.account = Account()
            db.add(user)
        db.commit()
        print(f"OK: {email} ({'admin' if user.is_admin else 'mijoz'})")


if __name__ == "__main__":
    main(sys.argv[1:])
