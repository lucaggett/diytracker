from app import app, db

with app.app_context():
    with db.engine.connect() as conn:
        conn.execute(db.text('ALTER TABLE submitter ADD COLUMN invite_token VARCHAR(64)'))
        conn.execute(db.text('ALTER TABLE submitter ADD COLUMN invite_token_expiry DATETIME'))
        conn.commit()
print('Migration complete')
