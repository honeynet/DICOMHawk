import os
import shutil
import logging
import config

app_logger = logging.getLogger("app_logger")
exceptions_logger = logging.getLogger("exceptions")

def should_use_fallback(tcia_username, tcia_password):
    """Check if TCIA fallback should be used"""
    # Check if fallback mode is disabled
    if not config.TCIA_FALLBACK_MODE:
        app_logger.info("TCIA fallback mode is disabled")
        return False
        
    # Check if TCIA is disabled via config
    if not config.TCIA_ACTIVATED:
        app_logger.info("TCIA is disabled, using fallback mode")
        return True
        
    # Check if credentials are missing or default
    if (not tcia_username or 
        not tcia_password or 
        tcia_username == "user" or 
        tcia_password == "pass"):
        app_logger.info("TCIA credentials are missing or default, using fallback mode")
        return True
        
    return False

def get_sample_tcia_directory():
    """Get the directory containing sample TCIA files"""
    return os.path.join(os.path.dirname(__file__), '..', 'storage', 'sample_tcia_data')

def copy_sample_files_to_tcia_directory(tcia_dir):
    """Copy sample TCIA files to TCIA directory structure"""
    try:
        app_logger.info("Copying sample TCIA files to TCIA directory...")
        
        # Clear existing TCIA directory contents (but not the directory itself)
        if os.path.exists(tcia_dir):
            for item in os.listdir(tcia_dir):
                item_path = os.path.join(tcia_dir, item)
                if os.path.isdir(item_path):
                    shutil.rmtree(item_path)
                else:
                    os.remove(item_path)
        else:
            # Create TCIA directory if it doesn't exist
            os.makedirs(tcia_dir)
        
        sample_dir = get_sample_tcia_directory()
        
        if not os.path.exists(sample_dir):
            app_logger.warning(f"Sample TCIA directory not found: {sample_dir}")
            return 0
        
        files_copied = 0
        
        for modality in config.MODALITIES:
            modality_sample_dir = os.path.join(sample_dir, modality)
            if not os.path.exists(modality_sample_dir):
                app_logger.warning(f"No sample files found for modality {modality}")
                continue
                
            modality_dir = os.path.join(tcia_dir, modality)
            os.makedirs(modality_dir)
            
            for study_uid in os.listdir(modality_sample_dir):
                study_sample_dir = os.path.join(modality_sample_dir, study_uid)
                if not os.path.isdir(study_sample_dir):
                    continue
                    
                for series_uid in os.listdir(study_sample_dir):
                    series_sample_dir = os.path.join(study_sample_dir, series_uid)
                    if not os.path.isdir(series_sample_dir):
                        continue
                        
                    from pydicom.uid import generate_uid
                    new_study_uid = generate_uid()
                    new_series_uid = generate_uid()
                       
                    study_dir = os.path.join(modality_dir, new_study_uid)
                    series_dir = os.path.join(study_dir, new_series_uid)
                    os.makedirs(series_dir)
                    
                    for filename in os.listdir(series_sample_dir):
                        if filename.endswith('.dcm'):
                            src_path = os.path.join(series_sample_dir, filename)
                            dest_path = os.path.join(series_dir, filename)
                            shutil.copy2(src_path, dest_path)
                            files_copied += 1
        
        app_logger.info(f"Copied {files_copied} sample TCIA files to TCIA directory")
        return files_copied
        
    except Exception as e:
        exceptions_logger.exception(f"Error copying sample TCIA files: {e}")
        return 0

def setup_fallback_data(tcia_username, tcia_password, tcia_dir):
    """Setup fallback data if TCIA is unavailable"""
    if should_use_fallback(tcia_username, tcia_password):
        app_logger.info("Setting up TCIA fallback data...")
        files_copied = copy_sample_files_to_tcia_directory(tcia_dir)
        if files_copied > 0:
            app_logger.info(f"Successfully set up {files_copied} fallback TCIA files")
            return True
        else:
            app_logger.warning("Failed to set up fallback data")
            return False
    return False 