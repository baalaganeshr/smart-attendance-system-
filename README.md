# Smart Attendance System - Mahalakshmi Women's College Edition

A Django-based smart attendance tracking system using **Face Recognition** and **Geofencing**. Custom configured for Mahalakshmi Women's College of Arts and Science.

## 🚀 Features

- **Face Recognition**: Verifies student identity using AI.
- **Geofencing**: Ensures students are physically present on campus (Mahalakshmi Nagar, Avadi).
- **Role-Based Access**:
  - **Super Admin**: Manage admins and system settings.
  - **Admin**: Manage students and view analytics.
  - **Student**: Mark attendance and view history.
- **Demo Mode**: One-click login for demonstration purposes.

---

## 🛠️ Installation Guide (Windows)

Follow these steps to set up the project on your local machine.

### 1. Prerequisites
- Python 3.10 or higher installed.
- Git (optional, for cloning).

### 2. Set Up Virtual Environment
Open your terminal (PowerShell or Command Prompt) in the project folder:

```powershell
# Create virtual environment
python -m venv venv

# Activate virtual environment
# Windows:
.\venv\Scripts\activate
# Mac/Linux:
# source venv/bin/activate
```

### 3. Install Dependencies
This project uses `dlib` for face recognition. On Windows, we use a pre-compiled binary to avoid complex build errors.

```powershell
# First, upgrade pip
python -m pip install --upgrade pip

# Install CMake (required for some packages)
pip install cmake

# Install Dependencies
pip install -r requirements.txt
```

> **Note:** If `dlib` fails to install, try installing the pre-built binary directly:
> `pip install dlib-bin`

### 4. Database Setup
Initialize the database and apply migrations.

```powershell
python manage.py migrate
```

### 5. Create Admin User (Optional)
You can create a superuser to access the admin panel, or use the **Demo Admin** button on the login page.

```powershell
python manage.py createsuperuser
# Follow the prompts to set username (e.g., admin) and password
```

---

## ▶️ Running the Application

1. Start the Django development server:
   ```powershell
   python manage.py runserver
   ```

2. Open your web browser and go to:
   **[http://127.0.0.1:8000/](http://127.0.0.1:8000/)**

---

## 📱 How to Use (Demo Mode)

We have added **Demo Buttons** on the login page for easy testing.

### 1. Login
- Click **"Demo Admin Login"**: Logs you in as an Administrator.
  - Use this to **View Attendance Logs** and **Manage Students**.
- Click **"Demo Student Login"**: Logs you in as a Student.
  - Use this to **Mark Attendance**.

### 2. Marking Attendance
1. Log in as a **Student**.
2. Click **"Mark Attendance"**.
3. **Location Check**:
   - The system checks if you are at **Mahalakshmi Women's College** (13.091499, 80.105168).
   - Radius: **150 meters**.
   - If you are testing from home (away from college), a popup will appear. Check the distance and click **"Proceed Anyway"** (this will mark you as *Absent/Location Failed*, but records the attempt).
4. **Camera Check**:
   - Allow camera permissions.
   - Click **"Start Camera"** -> **"Capture Selfie"**.
5. Click **"Mark Attendance"**.

### 3. Viewing Analytics (Admin)
1. Log in as an **Admin**.
2. Go to **Analytics** or **Dashboard**.
3. You will see the attendance records (including "Failed" attempts from remote locations).

---

## 📍 College Configuration
The system allows attendance **only** within the college campus.
- **Location**: No. 1, Mahalakshmi Nagar, Paruthipattu, Avadi, Chennai 600071.
- **Coordinates**: 13.091499° N, 80.105168° E

---

## ❓ Troubleshooting

**Camera not working?**
- Ensure you are using `http://127.0.0.1:8000` (localhost) or an HTTPS connection. Browsers block camera access on insecure HTTP connections (except localhost).
- Check browser permissions.

**"Proceed Anyway" button missing?**
- Clear your browser cache (Ctrl + F5).

**`dlib` installation error?**
- Make sure you installed `cmake` first.
- Try `pip install dlib-bin` instead of `dlib`.
