from flask import Flask
from config import SECRET_KEY, UPLOAD_FOLDER, MAX_CONTENT_LENGTH

def create_app():
    app = Flask(__name__)
    app.secret_key = SECRET_KEY
    app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
    app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH
    
    # Ensure upload folder exists
    import os
    if not os.path.exists(UPLOAD_FOLDER):
        os.makedirs(UPLOAD_FOLDER)
    
    # Register blueprints
    from Nexa.admin_routes import admin_bp
    app.register_blueprint(admin_bp, url_prefix='/admin')
    
    return app