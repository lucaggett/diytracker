import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app, db

with app.app_context():
    with db.engine.connect() as conn:
        conn.execute(db.text('ALTER TABLE event ADD COLUMN description TEXT'))
        conn.execute(db.text('ALTER TABLE event ADD COLUMN source_url VARCHAR(300)'))
        conn.commit()
print('Migration complete')
