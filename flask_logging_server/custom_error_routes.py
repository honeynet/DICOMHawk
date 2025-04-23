from flask import render_template, request, jsonify, Blueprint
import uuid
import datetime
import json
from flask_logging_server import error_config 

error_routes = Blueprint('error_routes', __name__)

@error_routes.route('/log_error_page_interaction', methods=['POST'])
def log_interaction():
    try:
        data = request.json
        error_code = data.get('error_code', 'unknown')
        request_id = data.get('request_id', 'unknown')
        time_spent = data.get('time_spent', 0)
        
        client_ip = request.remote_addr
        success = error_config.log_error_page_interaction(
            error_code, request_id, client_ip, time_spent
        )
        
        print(f"Received error page interaction: Error={error_code}, Request={request_id}, Time={time_spent}s")
        
        return jsonify({"success": success}), 200
    except Exception as e:
        print(f"Error logging interaction: {str(e)}")
        return jsonify({"error": str(e)}), 500
    
@error_routes.route('/trigger-error/<int:error_code>')
def trigger_error(error_code):
    
    print(f"Triggering error with code: {error_code}")
    
    allowed_codes = [400, 401, 403, 404, 500, 503]
    
    if error_code in allowed_codes:
        from flask import abort
        abort(error_code)
    else:
        return f"Error code {error_code} is not supported for testing. Supported codes: {', '.join(map(str, allowed_codes))}"

def register_error_handlers(app):
    print("Registering custom error handlers...")
    
    @app.errorhandler(400)
    def bad_request_error(error):
        print("Handling 400 error with custom error page")
        config = error_config.get_error_config(request, 400)
        return render_template('custom_error.html', **config), 400

    @app.errorhandler(401)
    def unauthorized_error(error):
        print("Handling 401 error with custom error page")
        config = error_config.get_error_config(request, 401)
        return render_template('custom_error.html', **config), 401

    @app.errorhandler(403)
    def forbidden_error(error):
        print("Handling 403 error with custom error page")
        config = error_config.get_error_config(request, 403)
        return render_template('custom_error.html', **config), 403

    @app.errorhandler(404)
    def not_found_error(error):
        print("Handling 404 error with custom error page")
        config = error_config.get_error_config(request, 404)
        return render_template('custom_error.html', **config), 404

    @app.errorhandler(500)
    def internal_server_error(error):
        print("Handling 500 error with custom error page")
        config = error_config.get_error_config(request, 500)
        return render_template('custom_error.html', **config), 500

    @app.errorhandler(503)
    def service_unavailable_error(error):
        print("Handling 503 error with custom error page")
        config = error_config.get_error_config(request, 503)
        return render_template('custom_error.html', **config), 503
    
    print("All custom error handlers registered successfully")
