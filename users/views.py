from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from django.contrib import messages
from django.db.models import Count, Q
from django.utils import timezone
from datetime import timedelta
from .models import User
from attendance.models import AttendanceLog
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
import os


def dashboard_redirect(request):
	if not request.user.is_authenticated:
		return redirect('users:login')

	role = getattr(request.user, 'role', '')
	if getattr(request.user, 'is_superuser', False) or role in ('super', 'superadmin'):
		return redirect('users:super_dashboard')
	if getattr(request.user, 'is_admin', False) or role == 'admin':
		return redirect('users:admin_dashboard')
	return redirect('users:student_dashboard')


@require_http_methods(["GET", "POST"])
def login_view(request):
	error_message = None
	if request.method == 'POST':
		# Demo login check
		if request.POST.get('demo_login') == 'true':
			try:
				# Try to find a demo/admin user or fallback to the first admin
				user = User.objects.filter(role__in=['admin', 'superadmin']).first()
				if user:
					login(request, user)
					return redirect('users:dashboard')
				else:
					# Create a demo admin if none exists
					user = User.objects.create_superuser('admin', 'admin@example.com', 'admin123')
					user.role = 'superadmin'
					user.save()
					login(request, user)
					return redirect('users:dashboard')
			except Exception as e:
				error_message = f'Demo login failed: {str(e)}'

		if request.POST.get('demo_student_login') == 'true':
			try:
				user = User.objects.filter(role='student').first()
				if user:
					login(request, user)
					return redirect('users:dashboard')
				else:
					# Create a demo student
					user = User.objects.create_user('student', 'student@example.com', 'student123')
					user.role = 'student'
					user.save()
					from users.models import StudentProfile
					# Create profile for new student
					try:
						StudentProfile.objects.create(
							user=user, jntu_no='DEMO001', name='Demo Student',
							company='Mahalakshmi Women\'s College of Arts and Science', lat=13.091499, lon=80.105168
						)
					except Exception:
						pass # JNTU might exist
					login(request, user)
					return redirect('users:dashboard')
			except Exception as e:
				error_message = f'Demo student login failed: {str(e)}'

		# Regular login logic
		if not (request.POST.get('demo_login') or request.POST.get('demo_student_login')):
			username = request.POST.get('username') or request.POST.get('email')
			password = request.POST.get('password')
			
			if username and password:
				user = authenticate(request, username=username, password=password)
				if user is not None:
					# Capture first-login before login() updates last_login
					is_first_login = user.last_login is None
					login(request, user)
					# Force onboarding for students on first login or if face not enrolled
					if getattr(user, 'is_student', False):
						try:
							face_enrolled = getattr(user.student_profile, 'face_enrolled', False)
						except Exception:
							face_enrolled = False
						if is_first_login or not face_enrolled:
							return redirect('users:student_onboarding')
					return redirect('users:dashboard')
				else:
					# Custom logic for error message
					try:
						User.objects.get(username=username)
						error_message = 'Invalid password.'
					except User.DoesNotExist:
						error_message = 'Invalid email.'
			else:
				error_message = 'Please provide credentials.'
	
	return render(request, 'users/login.html', {'error': error_message})


def logout_view(request):
	logout(request)
	return redirect('users:login')


@login_required
def super_dashboard(request):
	# Restrict to super admin/superuser only
	role = getattr(request.user, 'role', '')
	if not (getattr(request.user, 'is_superuser', False) or role in ('super', 'superadmin')):
		messages.error(request, 'You are not authorized to view the Super Admin dashboard.')
		return redirect('users:dashboard')

	# Get real data counts
	total_admins = User.objects.filter(role='admin').count()
	total_students = User.objects.filter(role='student').count()
	total_attendance = AttendanceLog.objects.count()
	
	# Calculate attendance rate for last 30 days
	thirty_days_ago = timezone.now().date() - timedelta(days=30)
	recent_attendance = AttendanceLog.objects.filter(date__gte=thirty_days_ago)
	total_recent = recent_attendance.count()
	present_recent = recent_attendance.filter(status='present').count()
	
	attendance_percentage = 0
	if total_recent > 0:
		attendance_percentage = round((present_recent / total_recent) * 100, 1)
	
	context = {
		'total_admins': total_admins,
		'total_students': total_students,
		'total_attendance': total_attendance,
		'attendance_percentage': f"{attendance_percentage}%"
	}
	return render(request, 'super/dashboard.html', context)


@login_required
def admin_dashboard(request):
	return render(request, 'admin/dashboard.html')


@login_required
def student_dashboard(request):
	# Only students can access this
	if not getattr(request.user, 'is_student', False):
		return redirect('users:dashboard')
	
	# Get student profile
	profile = getattr(request.user, 'student_profile', None)
	
	# Get today's attendance
	today_attendance = None
	attendance_status = None
	attendance_time = None
	total_time_display = None
	
	if profile:
		try:
			from attendance.models import AttendanceLog
			from datetime import date
			# Consider all of today's attempts to avoid choosing an older failed row
			logs_today = AttendanceLog.objects.filter(
				student=profile,
				date=date.today()
			).order_by('-updated_at', '-time')
			
			today_attendance = logs_today.first()
			if today_attendance:
				# Check if both check-in and check-out are completed
				if today_attendance.check_in_time and today_attendance.check_out_time:
					# Both completed - attendance marked successfully
					attendance_status = 'completed'
					attendance_time = today_attendance.check_out_time
					# Calculate total time
					if today_attendance.total_time_minutes:
						hours = today_attendance.total_time_minutes // 60
						minutes = today_attendance.total_time_minutes % 60
						if hours > 0:
							total_time_display = f"{hours}h {minutes}m"
						else:
							total_time_display = f"{minutes}m"
				elif today_attendance.check_in_time:
					# Only check-in completed - pending check-out
					attendance_status = 'pending'
					attendance_time = today_attendance.check_in_time
				else:
					# No check-in - failed attempts or initial state
					any_present = logs_today.filter(geofence_verified=True, face_verified=True).exists()
					if any_present:
						attendance_status = 'present'
					else:
						if getattr(today_attendance, 'geofence_verified', False) and getattr(today_attendance, 'face_verified', False):
							attendance_status = 'present'
						elif not getattr(today_attendance, 'geofence_verified', False) and not getattr(today_attendance, 'face_verified', False):
							attendance_status = 'fail_both'
						elif not getattr(today_attendance, 'geofence_verified', False):
							attendance_status = 'fail_geo'
						else:
							attendance_status = 'fail_face'
					attendance_time = today_attendance.time
		except Exception as e:
			print(f"Error in attendance status: {str(e)}")
			pass
	
	# Get weekly attendance
	weekly_attendance = []
	if profile:
		try:
			from attendance.models import AttendanceLog
			from datetime import date, timedelta
			
			week_start = date.today() - timedelta(days=7)
			weekly_attendance = AttendanceLog.objects.filter(
				student=profile,
				date__gte=week_start
			).order_by('-date', '-time')
		except Exception:
			pass
	
	# Calculate working days and attendance statistics
	total_working_days = 0
	days_attended = 0
	attendance_percentage = 0
	
	if profile:
		try:
			# Get all unique dates where at least one student completed check-out (both check-in and check-out)
			# This represents the common working days
			all_dates = AttendanceLog.objects.filter(
				check_in_time__isnull=False,
				check_out_time__isnull=False
			).values_list('date', flat=True).distinct().order_by('date')
			total_working_days = all_dates.count()
			
			# Get distinct dates when this student completed both check-in AND check-out
			attended_dates = AttendanceLog.objects.filter(
				student=profile,
				check_in_time__isnull=False,
				check_out_time__isnull=False
			).values_list('date', flat=True).distinct()
			days_attended = attended_dates.count()
			
			# Calculate attendance percentage
			if total_working_days > 0:
				attendance_percentage = round((days_attended / total_working_days) * 100, 2)
		except Exception as e:
			print(f"Error calculating working days: {str(e)}")
			pass
	
	context = {
		'profile': profile,
		'today_attendance': today_attendance,
		'attendance_status': attendance_status,
		'attendance_time': attendance_time,
		'total_time_display': total_time_display,
		'weekly_attendance': weekly_attendance,
		'total_working_days': total_working_days,
		'days_attended': days_attended,
		'attendance_percentage': attendance_percentage,
	}
	
	return render(request, 'student/dashboard.html', context)


@login_required
@require_http_methods(["GET", "POST"])
def student_onboarding(request):
	# Only students can access onboarding
	if not getattr(request.user, 'is_student', False):
		return redirect('users:dashboard')

	error = None
	if request.method == 'POST':
		new_password = request.POST.get('new_password', '').strip()
		confirm_password = request.POST.get('confirm_password', '').strip()
		photo_file = request.FILES.get('photo')

		if not new_password or not confirm_password:
			error = 'Please enter and confirm your new password.'
		elif new_password != confirm_password:
			error = 'Passwords do not match.'
		elif len(new_password) < 6:
			error = 'Password must be at least 6 characters.'

		if not error and not photo_file:
			error = 'Please upload your photo.'

		if not error:
			try:
				# Update password
				request.user.set_password(new_password)
				request.user.save()

				# Save photo to media storage and generate face encoding
				profile = request.user.student_profile
				ext = os.path.splitext(photo_file.name)[1] or '.jpg'
				filename = f"faces/{str(profile.id)}{ext}"
				path = default_storage.save(filename, ContentFile(photo_file.read()))
				profile.reference_image_url = default_storage.url(path) if hasattr(default_storage, 'url') else f"/media/{path}"
				
				# Generate face encoding for the uploaded image
				try:
					import face_recognition
					import numpy as np
					from PIL import Image
					import io
					
					# Read the uploaded image
					photo_file.seek(0)  # Reset file pointer
					image_data = photo_file.read()
					
					# Load image using PIL from bytes
					pil_image = Image.open(io.BytesIO(image_data))
					
					# Convert PIL image to RGB if necessary
					if pil_image.mode != 'RGB':
						pil_image = pil_image.convert('RGB')
					
					# Convert to numpy array for face_recognition
					face_image = np.array(pil_image)
					
					# Detect face locations in the image
					face_locations = face_recognition.face_locations(face_image)
					
					if not face_locations:
						error = 'No face detected in the uploaded image. Please upload a clear photo with your face visible.'
					elif len(face_locations) > 1:
						error = 'Multiple faces detected in the image. Please upload a photo with only your face.'
					else:
						# Generate face encoding
						face_encodings = face_recognition.face_encodings(face_image, face_locations)
						
						if not face_encodings:
							error = 'Could not generate face encoding. Please try with a clearer image.'
						else:
							# Store the face encoding
							profile.face_encoding = face_encodings[0].tolist()
							profile.face_enrolled = True
							profile.save()
							
							messages.success(request, 'Onboarding complete. Face encoding generated successfully. Please sign in again with your new password.')
							logout(request)
							return redirect('users:login')
							
				except ImportError:
					# Fallback: Use face service or just mark as enrolled
					try:
						import requests
						
						# Try to use face service
						photo_file.seek(0)  # Reset file pointer
						image_data = photo_file.read()
						
						files = {'image': ('photo.jpg', image_data, 'image/jpeg')}
						data = {'student_id': str(profile.id)}
						
						face_service_url = 'http://localhost:8001'  # Default face service URL
						response = requests.post(
							f"{face_service_url}/face/enroll",
							files=files,
							data=data,
							timeout=30
						)
						
						if response.status_code == 200:
							result = response.json()
							if result.get('success'):
								profile.face_enrolled = True
								profile.save()
								messages.success(request, 'Onboarding complete. Face enrolled with face service. Please sign in again with your new password.')
								logout(request)
								return redirect('users:login')
							else:
								error = f'Face service enrollment failed: {result.get("message", "Unknown error")}'
						else:
							error = f'Face service request failed: {response.status_code}'
							
					except Exception as e:
						# Final fallback: just mark as enrolled without encoding
						profile.face_enrolled = True
						profile.save()
						messages.success(request, 'Onboarding complete. Please sign in again with your new password.')
						logout(request)
						return redirect('users:login')
				except Exception as e:
					error = f'Failed to process face image: {str(e)}'
			except Exception as e:
				error = f'Failed to complete onboarding: {str(e)}'

	return render(request, 'student/onboarding.html', {'error': error})


@login_required
def student_profile(request):
	"""Student profile view."""
	# Only students can access this
	if not getattr(request.user, 'is_student', False):
		return redirect('users:dashboard')
	
	# Get student profile
	profile = getattr(request.user, 'student_profile', None)
	
	# If no profile or face not enrolled, redirect to onboarding
	if not profile or not profile.face_enrolled:
		return redirect('users:student_onboarding')
	
	# Get attendance history
	attendance_history = []
	if profile:
		try:
			from attendance.models import AttendanceLog
			attendance_history = AttendanceLog.objects.filter(
				student=profile
			).order_by('-date', '-time')[:10]  # Last 10 records
		except Exception:
			pass
	
	context = {
		'profile': profile,
		'attendance_history': attendance_history,
	}
	
	return render(request, 'student/profile.html', context)


@login_required
def contact_admin(request):
	"""Contact admin functionality."""
	# Only students can access this
	if not getattr(request.user, 'is_student', False):
		return redirect('users:dashboard')
	
	error = None
	if request.method == 'POST':
		subject = request.POST.get('subject', '').strip()
		message = request.POST.get('message', '').strip()
		
		if not subject or not message:
			error = 'Please fill in both subject and message.'
		else:
			try:
				# Create contact message
				from attendance.models import ContactMessage
				student_profile = request.user.student_profile
				ContactMessage.objects.create(
					student=student_profile,
					subject=subject,
					message=message
				)
				messages.success(request, 'Your message has been sent to admin successfully!')
				return redirect('users:student_dashboard')
			except Exception as e:
				error = f'Failed to send message: {str(e)}'
	
	return render(request, 'student/contact_admin.html', {'error': error})


@login_required
def manage_admins(request):
	# Restrict to super admin/superuser only
	role = getattr(request.user, 'role', '')
	if not (getattr(request.user, 'is_superuser', False) or role in ('super', 'superadmin')):
		messages.error(request, 'Only Super Admin can manage admins.')
		return redirect('users:admin_dashboard')

	if request.method == 'POST':
		email = request.POST.get('email')
		first_name = request.POST.get('first_name')
		last_name = request.POST.get('last_name')
		password = request.POST.get('password')
		
		if email and first_name and password:
			try:
				# Check if admin already exists
				if User.objects.filter(email=email).exists():
					messages.error(request, f'Admin with email {email} already exists!')
				else:
					# Create new admin
					admin = User.objects.create_user(
						email=email,
						username=email,
						first_name=first_name,
						last_name=last_name,
						role='admin',
						is_staff=True
					)
					admin.set_password(password)
					admin.save()
					
					messages.success(request, f'New admin account created successfully for {first_name} {last_name}!')
					return redirect('users:manage_admins')
			except Exception as e:
				messages.error(request, f'Error creating admin: {str(e)}')
		else:
			messages.error(request, 'Please fill all required fields!')
	
	# Get existing admins
	admins = User.objects.filter(role='admin').order_by('-date_joined')
	context = {'admins': admins}
	return render(request, 'super/manage_admins.html', context)


@login_required
def edit_admin(request, admin_id):
	# Allow only superadmin/superuser to edit admins
	user_role = getattr(request.user, 'role', '')
	if not (getattr(request.user, 'is_superuser', False) or user_role in ('super', 'superadmin')):
		messages.error(request, 'You do not have permission to edit admins.')
		return redirect('users:dashboard')

	try:
		admin_user = User.objects.get(id=admin_id, role='admin')
	except User.DoesNotExist:
		messages.error(request, 'Admin not found.')
		return redirect('users:manage_admins')

	if request.method == 'POST':
		first_name = request.POST.get('first_name', '').strip()
		last_name = request.POST.get('last_name', '').strip()
		email = request.POST.get('email', '').strip()
		is_active = request.POST.get('is_active') == 'on'
		password = request.POST.get('password', '')

		# Validate unique email if changed
		if email and email != admin_user.email and User.objects.filter(email=email).exists():
			messages.error(request, f'Another user with email {email} already exists!')
		else:
			admin_user.first_name = first_name or admin_user.first_name
			admin_user.last_name = last_name or admin_user.last_name
			if email:
				admin_user.email = email
				admin_user.username = email
			admin_user.is_active = is_active
			if password:
				admin_user.set_password(password)
			admin_user.save()
			messages.success(request, 'Admin updated successfully!')
			return redirect('users:manage_admins')

	context = {
		'admin_user': admin_user,
	}
	return render(request, 'super/edit_admin.html', context)


@login_required
@require_http_methods(["POST"])
def delete_admin(request, admin_id):
	# Only superadmin/superuser can delete admins
	user_role = getattr(request.user, 'role', '')
	if not (getattr(request.user, 'is_superuser', False) or user_role in ('super', 'superadmin')):
		messages.error(request, 'You do not have permission to delete admins.')
		return redirect('users:manage_admins')

	# Disallow deleting own account for safety
	if str(request.user.id) == str(admin_id):
		messages.error(request, 'You cannot delete your own account.')
		return redirect('users:manage_admins')

	try:
		admin_user = User.objects.get(id=admin_id, role='admin')
		admin_user.delete()
		messages.success(request, 'Admin deleted successfully.')
	except User.DoesNotExist:
		messages.error(request, 'Admin not found.')
	except Exception as e:
		messages.error(request, f'Failed to delete admin: {str(e)}')

	return redirect('users:manage_admins')


@login_required
def admin_settings(request):
	# Restrict to super admin/superuser only
	role = getattr(request.user, 'role', '')
	if not (getattr(request.user, 'is_superuser', False) or role in ('super', 'superadmin')):
		messages.error(request, 'Only Super Admin can access system settings.')
		return redirect('users:admin_dashboard')

	return render(request, 'super/dashboard.html', {'section': 'settings'})
