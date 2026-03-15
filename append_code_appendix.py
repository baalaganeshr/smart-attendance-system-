import os
import html
import re

def read_file(path):
    try:
        # Use relative paths as absolute paths would be tedious to manually construct
        # But wait, this script will run in the current working directory
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        return f"# Error reading file {path}: {str(e)}"

def create_chap_11(title, description, content_path):
    code_content = read_file(content_path)
    # Basic HTML escaping for code
    clean_code = html.escape(code_content)
    
    # We'll return HTML for this section
    return f"""
    <div class="page-break"></div>
    <h2>{title} ({content_path})</h2>
    <p>{description}</p>
    <pre style="background-color: #f8f9fa; padding: 10px; border: 1px solid #dee2e6; border-radius: 4px; overflow-x: auto; white-space: pre-wrap; font-size: 11px; font-family: 'Consolas', 'Monaco', monospace;"><code>{clean_code}</code></pre>
    """

def get_code_appendix_html():
    
    intro_html = """
    <div class="page-break"></div>
    <!-- CHAPTER 11 -->
    <h1>CHAPTER 11: SOURCE CODE LISTING</h1>
    <p>The complete source code for the <strong>Smart Attendance System</strong> is available at the following GitHub repository:</p>
    <p><strong>Repository URL:</strong> <a href="https://github.com/baalaganeshr/smart-attendance-system-">https://github.com/baalaganeshr/smart-attendance-system-</a></p>

    <p>Below are the core components of the system implementation. These files form the backbone of the application logic, data models, and the AI-powered facial recognition service.</p>
    """

    sections = []
    
    sections.append(create_chap_11(
        "11.1 Attendance Models", 
        "Defines the database schema for Companies, Attendance Logs, and Geofence Settings.",
        "attendance/models.py"
    ))

    # For large file 'views.py', maybe we should trim it? No, user said "put all".
    sections.append(create_chap_11(
        "11.2 Attendance Views", 
        "Handles the logic for marking attendance, verifying geofences, and processing facial recognition data.",
        "attendance/views.py"
    ))

    sections.append(create_chap_11(
        "11.3 User Models", 
        "Manages user authentication, roles (Student, Admin, Superadmin), and detailed student profiles.",
        "users/models.py"
    ))

    sections.append(create_chap_11(
        "11.4 Face Service Microservice", 
        "A standalone microservice using FastAPI and Dlib for high-performance face encoding and verification.",
        "face_service/main.py"
    ))
    
    sections.append(create_chap_11(
        "11.5 Main Settings", 
        "Global configuration for Django, including database, middleware, and installed apps.",
        "attendance_system/settings.py"
    ))

    return intro_html + "".join(sections)

def main():
    report_path = 'project_report.html'
    
    if not os.path.exists(report_path):
        print("Error: project_report.html not found.")
        return

    content = read_file(report_path)
    
    # Check if we already appended Chapter 11
    if "CHAPTER 11: SOURCE CODE LISTING" in content:
        print("Chapter 11 already found. Skipping append to prevent duplication.")
    else:
        # Find insertion point before </body>
        insertion_marker = "</body>"
        if insertion_marker in content:
            appendix = get_code_appendix_html()
            new_content = content.replace(insertion_marker, f"{appendix}\n{insertion_marker}")
            
            with open(report_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
            print("Successfully appended Chapter 11 to project_report.html")
        else:
            print("Error: Could not find </body> tag.")

if __name__ == "__main__":
    main()
