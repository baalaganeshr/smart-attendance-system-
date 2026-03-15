"""
Views for the attendance app.
"""

import csv
import pandas as pd
from datetime import datetime, date
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.conf import settings
from django.utils import timezone
from django.core.paginator import Paginator
from django.db import models
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from geopy.distance import geodesic
# import requests  # No longer needed for local face verification
import re
from .models import (
    AttendanceLog, GeofenceSettings, SystemSettings, ContactMessage, Company
)
from .serializers import (
    AttendanceLogSerializer, AttendanceMarkSerializer, GeofenceSettingsSerializer,
    SystemSettingsSerializer, ContactMessageSerializer, ContactMessageResponseSerializer,
    StudentUploadSerializer, StudentLocationUpdateSerializer, BulkLocationUpdateSerializer,
    AttendanceExportSerializer
)
from users.models import User, StudentProfile

def is_admin(user):
    """Check if user is admin."""
    return user.is_authenticated and user.is_admin

def is_student(user):
    """Check if user is student."""
    return user.is_authenticated and user.is_student


@login_required
@user_passes_test(is_admin)
def upload_students(request):
    """Upload student data via CSV/XLSX."""
    if request.method == 'POST':
        serializer = StudentUploadSerializer(data=request.FILES)
        if serializer.is_valid():
            file = serializer.validated_data['file']
            
            try:
                if file.name.endswith('.csv'):
                    df = pd.read_csv(file)
                else:
                    df = pd.read_excel(file)
                
                # Expected columns now: roll_number, name, email, company (mandatory)
                required_columns = ['roll_number', 'name', 'email', 'company']
                if not all(col in df.columns for col in required_columns):
                    messages.error(request, 'File must contain: roll_number, name, email, company columns')
                    return render(request, 'admin/upload_students.html')
                
                success_count = 0
                error_count = 0
                
                for _, row in df.iterrows():
                    try:
                        # Check if student already exists (by roll number)
                        if StudentProfile.objects.filter(jntu_no=row['roll_number']).exists():
                            continue
                        # Resolve company from master table
                        company_name = str(row['company']).strip()
                        try:
                            company_obj = Company.objects.get(name__iexact=company_name)
                        except Company.DoesNotExist:
                            error_count += 1
                            continue

                        # Create user
                        user = User.objects.create_user(
                            email=row['email'],
                            first_name=row['name'].split()[0] if ' ' in str(row['name']) else row['name'],
                            last_name=row['name'].split()[-1] if ' ' in str(row['name']) else '',
                            username=row['email'],
                            role='student'
                        )
                        # Set default password to roll number
                        user.set_password(str(row['roll_number']))
                        user.save()
                        
                        # Create student profile
                        profile = StudentProfile.objects.create(
                            user=user,
                            jntu_no=row['roll_number'],
                            name=row['name'],
                            company=company_obj.name,
                            lat=company_obj.lat,
                            lon=company_obj.lon
                        )
                        
                        success_count += 1
                        
                    except Exception as e:
                        error_count += 1
                        print(f"Error creating student {row.get('jntu_no', 'unknown')}: {str(e)}")
                
                messages.success(request, f'Successfully created {success_count} students. Errors: {error_count}')
                
            except Exception as e:
                messages.error(request, f'Error processing file: {str(e)}')
    
    return render(request, 'admin/upload_students.html')


@login_required
@user_passes_test(is_admin)
def manage_students(request):
    """Manage student information and locations."""
    students = StudentProfile.objects.select_related('user').all().order_by('-created_at')
    # Optional filter by company name
    company_filter = request.GET.get('company')
    if company_filter:
        students = students.filter(company=company_filter)
    
    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'update_location':
            student_id = request.POST.get('student_id')
            lat = request.POST.get('lat')
            lon = request.POST.get('lon')
            
            try:
                student = StudentProfile.objects.get(id=student_id)
                
                # Handle empty string values for lat/lon fields
                if lat and lat.strip():
                    try:
                        student.lat = float(lat)
                    except ValueError:
                        messages.error(request, 'Invalid latitude value')
                        return redirect('attendance:manage_students')
                else:
                    student.lat = None
                
                if lon and lon.strip():
                    try:
                        student.lon = float(lon)
                    except ValueError:
                        messages.error(request, 'Invalid longitude value')
                        return redirect('attendance:manage_students')
                else:
                    student.lon = None
                
                student.save()
                messages.success(request, f'Location updated for {student.name}')
            except StudentProfile.DoesNotExist:
                messages.error(request, 'Student not found')
        
        elif action == 'bulk_update':
            company = request.POST.get('company')
            lat = request.POST.get('lat')
            lon = request.POST.get('lon')
            
            if company and lat and lon:
                try:
                    lat_value = float(lat) if lat.strip() else None
                    lon_value = float(lon) if lon.strip() else None
                    updated = StudentProfile.objects.filter(company=company).update(lat=lat_value, lon=lon_value)
                    messages.success(request, f'Updated location for {updated} students in {company}')
                except ValueError:
                    messages.error(request, 'Invalid latitude or longitude values')
            else:
                messages.error(request, 'Please provide company, latitude, and longitude values')
        
        elif action == 'delete_selected':
            ids = request.POST.getlist('selected_ids')
            if ids:
                try:
                    deleted_count = 0
                    for student_id in ids:
                        try:
                            student_profile = StudentProfile.objects.get(id=student_id)
                            # Delete the associated User first, then StudentProfile
                            user = student_profile.user
                            student_profile.delete()  # This should cascade delete the User
                            # If cascade doesn't work, explicitly delete the user
                            if User.objects.filter(id=user.id).exists():
                                user.delete()
                            deleted_count += 1
                        except StudentProfile.DoesNotExist:
                            continue
                    
                    messages.success(request, f'Successfully deleted {deleted_count} selected student records')
                except Exception as e:
                    messages.error(request, f'Error deleting selected students: {str(e)}')
            else:
                messages.error(request, 'No students selected')
        
        elif action == 'reset_password':
            student_id = request.POST.get('student_id')
            new_password = request.POST.get('new_password')
            
            if not student_id or not new_password:
                messages.error(request, 'Student ID and new password are required')
            else:
                try:
                    student_profile = StudentProfile.objects.get(id=student_id)
                    user = student_profile.user
                    
                    # Validate password length
                    if len(new_password) < 6:
                        messages.error(request, 'Password must be at least 6 characters long')
                    else:
                        user.set_password(new_password)
                        user.save()
                        messages.success(request, f'Password reset successfully for {student_profile.name}')
                        
                except StudentProfile.DoesNotExist:
                    messages.error(request, 'Student not found')
                except Exception as e:
                    messages.error(request, f'Error resetting password: {str(e)}')
        
        elif action == 'bulk_reset_password':
            new_password = request.POST.get('new_password')
            selected_ids = request.POST.getlist('selected_ids')
            
            if not new_password or not selected_ids:
                messages.error(request, 'Password and selected students are required')
            else:
                if len(new_password) < 6:
                    messages.error(request, 'Password must be at least 6 characters long')
                else:
                    try:
                        updated_count = 0
                        for student_id in selected_ids:
                            try:
                                student_profile = StudentProfile.objects.get(id=student_id)
                                user = student_profile.user
                                user.set_password(new_password)
                                user.save()
                                updated_count += 1
                            except StudentProfile.DoesNotExist:
                                continue
                        
                        messages.success(request, f'Password reset successfully for {updated_count} students')
                    except Exception as e:
                        messages.error(request, f'Error resetting passwords: {str(e)}')
        
        elif action == 'delete_all':
            try:
                # Count students before deletion
                student_profiles_count = StudentProfile.objects.count()
                student_users_count = User.objects.filter(role='student').count()
                
                # Delete all StudentProfile records (this should cascade delete User records)
                StudentProfile.objects.all().delete()
                
                # If cascade didn't work, manually delete remaining student users
                remaining_student_users = User.objects.filter(role='student').count()
                if remaining_student_users > 0:
                    User.objects.filter(role='student').delete()
                
                messages.success(request, f'Successfully deleted all {student_profiles_count} student profiles and {student_users_count} student user accounts from database')
            except Exception as e:
                messages.error(request, f'Error deleting all students: {str(e)}')
    
    # Pagination
    paginator = Paginator(students, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'page_obj': page_obj,
        'companies': StudentProfile.objects.values_list('company', flat=True).distinct()
    }
    return render(request, 'admin/manage_students.html', context)


@login_required
@user_passes_test(is_admin)
def attendance_analytics(request):
    """Enhanced attendance analytics with time tracking."""
    from_date = request.GET.get('from_date')
    to_date = request.GET.get('to_date')
    company = request.GET.get('company')
    
    # Default to last 30 days
    if not from_date:
        from_date = (date.today() - timezone.timedelta(days=30)).strftime('%Y-%m-%d')
    if not to_date:
        to_date = date.today().strftime('%Y-%m-%d')
    
    # Filter attendance logs
    attendance_query = AttendanceLog.objects.filter(
        date__range=[from_date, to_date]
    ).select_related('student')
    
    if company:
        attendance_query = attendance_query.filter(student__company=company)
    
    # Get statistics
    total_attendance = attendance_query.count()
    present_count = attendance_query.filter(status='present').count()
    fail_geo_count = attendance_query.filter(status='fail_geo').count()
    fail_face_count = attendance_query.filter(status='fail_face').count()
    fail_both_count = attendance_query.filter(status='fail_both').count()
    
    # Time tracking statistics
    completed_sessions = attendance_query.filter(
        check_in_time__isnull=False, 
        check_out_time__isnull=False
    )
    total_time_minutes = completed_sessions.aggregate(
        total=models.Sum('total_time_minutes')
    )['total'] or 0
    
    avg_time_minutes = completed_sessions.aggregate(
        avg=models.Avg('total_time_minutes')
    )['avg'] or 0
    
    # Calculate total working days (common to all students)
    # Only count days where at least one student completed both check-in AND check-out
    total_working_days = attendance_query.filter(
        check_in_time__isnull=False,
        check_out_time__isnull=False
    ).values('date').distinct().count()
    
    # Student time tracking data with working days and attendance
    student_time_data = []
    for student in StudentProfile.objects.all():
        student_attendance = attendance_query.filter(student=student)
        student_total_time = student_attendance.aggregate(
            total=models.Sum('total_time_minutes')
        )['total'] or 0
        
        # Count days with check-in only (for reference)
        student_days_with_checkin = student_attendance.filter(
            check_in_time__isnull=False
        ).count()
        
        # Count distinct dates where student completed both check-in AND check-out
        student_days_present = student_attendance.filter(
            check_in_time__isnull=False,
            check_out_time__isnull=False
        ).values('date').distinct().count()
        
        avg_time_per_day = 0
        if student_days_present > 0:
            avg_time_per_day = student_total_time / student_days_present
        
        attendance_rate = 0
        if total_working_days > 0:
            attendance_rate = round((student_days_present / total_working_days) * 100, 2)
        
        # Include all students, even if they have no attendance
            student_time_data.append({
                'student': student,
                'total_time_minutes': student_total_time,
                'total_time_hours': student_total_time / 60,
            'days_present': student_days_present,
            'days_with_checkin': student_days_with_checkin,
                'avg_time_per_day': avg_time_per_day,
            'avg_time_per_day_hours': avg_time_per_day / 60,
            'attendance_rate': attendance_rate
            })
    
    # Sort by attendance rate first, then by total time
    student_time_data.sort(key=lambda x: (x['attendance_rate'], x['total_time_minutes']), reverse=True)
    
    # Daily attendance data for charts
    daily_data = attendance_query.values('date').annotate(
        present=models.Count('id', filter=models.Q(status='present')),
        total=models.Count('id')
    ).order_by('date')
    
    context = {
        'from_date': from_date,
        'to_date': to_date,
        'company': company,
        'total_attendance': total_attendance,
        'present_count': present_count,
        'fail_geo_count': fail_geo_count,
        'fail_face_count': fail_face_count,
        'fail_both_count': fail_both_count,
        'daily_data': list(daily_data),
        'companies': StudentProfile.objects.values_list('company', flat=True).distinct(),
        # Time tracking data
        'completed_sessions': completed_sessions.count(),
        'total_time_minutes': total_time_minutes,
        'avg_time_minutes': avg_time_minutes,
        'total_time_hours': total_time_minutes / 60,
        'avg_time_hours': avg_time_minutes / 60,
        'total_working_days': total_working_days,
        'student_time_data': student_time_data[:20],  # Top 20 students
    }
    return render(request, 'admin/analytics.html', context)


@login_required
@user_passes_test(is_admin)
def export_attendance(request):
    """Export attendance data to CSV."""
    from_date = request.GET.get('from_date')
    to_date = request.GET.get('to_date')
    company = request.GET.get('company')
    
    # Filter attendance logs
    attendance_query = AttendanceLog.objects.filter(
        date__range=[from_date, to_date]
    ).select_related('student')
    
    if company:
        attendance_query = attendance_query.filter(student__company=company)
    
    # Create CSV response
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="attendance_{from_date}_to_{to_date}.csv"'
    
    writer = csv.writer(response)
    writer.writerow([
        'Date', 'Time', 'Student Name', 'JNTU Number', 'Company', 'Status',
        'Latitude', 'Longitude', 'GPS Accuracy', 'Geofence Verified', 'Face Verified'
    ])
    
    for log in attendance_query:
        writer.writerow([
            log.date, log.time, log.student.name, log.student.jntu_no,
            log.student.company, log.get_status_display(), log.lat, log.lon,
            log.accuracy, log.geofence_verified, log.face_verified
        ])
    
    return response


@login_required
@user_passes_test(is_admin)
def contact_messages(request):
    """View and respond to student contact messages."""
    messages_list = ContactMessage.objects.select_related('student', 'responded_by').all().order_by('-created_at')
    
    if request.method == 'POST':
        message_id = request.POST.get('message_id')
        response_text = request.POST.get('response')
        
        if message_id and response_text:
            try:
                message = ContactMessage.objects.get(id=message_id)
                message.mark_responded(request.user, response_text)
                messages.success(request, 'Response sent successfully')
            except ContactMessage.DoesNotExist:
                messages.error(request, 'Message not found')
    
    # Pagination
    paginator = Paginator(messages_list, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {'page_obj': page_obj}
    return render(request, 'admin/contact_messages.html', context)


@login_required
@user_passes_test(is_admin)
def manage_companies(request):
    """Add and list companies for mapping students."""
    def parse_coordinate(value: str, is_lat: bool) -> float:
        """Parse decimal or DMS strings (e.g., 17°42′15″N, 82 59 30 E) to decimal degrees.

        Supports symbols: °, º, deg; ′, ', minutes; ″, ", seconds; optional N/S/E/W.
        """
        if value is None:
            raise ValueError('Empty coordinate')
        s = str(value).strip().upper()
        if not s:
            raise ValueError('Empty coordinate')

        # Try simple float first
        try:
            return float(s)
        except ValueError:
            pass

        # Normalize unicode symbols
        s = (
            s.replace('DEG', '°')
             .replace('º', '°')
             .replace('˚', '°')
             .replace('’', "'")
             .replace('′', "'")
             .replace('”', '"')
             .replace('″', '"')
        )

        # Regex for DMS: degrees minutes seconds optional direction
        m = re.match(r"^\s*(\d+(?:\.\d+)?)\D+(\d+(?:\.\d+)?)?\D*(\d+(?:\.\d+)?)?\s*([NSEW])?\s*$", s)
        if not m:
            # Alternative: space separated
            m = re.match(r"^\s*(\d+(?:\.\d+)?)(?:\s+(\d+(?:\.\d+)?))?(?:\s+(\d+(?:\.\d+)?))?\s*([NSEW])?\s*$", s)
        if not m:
            raise ValueError('Invalid coordinate format')

        deg = float(m.group(1))
        minutes = float(m.group(2)) if m.group(2) is not None else 0.0
        seconds = float(m.group(3)) if m.group(3) is not None else 0.0
        direction = m.group(4)

        decimal = deg + (minutes / 60.0) + (seconds / 3600.0)
        if direction in ('S', 'W'):
            decimal = -decimal

        # Validate range
        if is_lat and not (-90.0 <= decimal <= 90.0):
            raise ValueError('Latitude out of range (-90..90)')
        if not is_lat and not (-180.0 <= decimal <= 180.0):
            raise ValueError('Longitude out of range (-180..180)')
        return decimal

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        address = request.POST.get('address', '').strip()
        phone = request.POST.get('phone', '').strip()
        email = request.POST.get('email', '').strip()
        lat_raw = request.POST.get('lat')
        lon_raw = request.POST.get('lon')
        radius = request.POST.get('radius') or 150
        if name and lat_raw and lon_raw:
            try:
                lat_dd = parse_coordinate(lat_raw, is_lat=True)
                lon_dd = parse_coordinate(lon_raw, is_lat=False)
                Company.objects.create(
                    name=name, lat=lat_dd, lon=lon_dd, radius=radius,
                    address=address, phone=phone, email=email
                )
                messages.success(request, f'Company {name} added successfully')
            except Exception as e:
                messages.error(request, f'Failed to add company: {str(e)}')
        else:
            messages.error(request, 'Please fill all required fields')

    companies = Company.objects.all().order_by('name')
    return render(request, 'admin/companies.html', {'companies': companies})


@login_required
@user_passes_test(is_admin)
def edit_company(request, company_id):
    """Edit an existing company."""
    try:
        company = Company.objects.get(id=company_id)
    except Company.DoesNotExist:
        messages.error(request, 'Company not found')
        return redirect('attendance:manage_companies')
    
    def parse_coordinate(value: str, is_lat: bool) -> float:
        """Parse decimal or DMS strings (e.g., 17°42′15″N, 82 59 30 E) to decimal degrees.

        Supports symbols: °, º, deg; ′, ', minutes; ″, ", seconds; optional N/S/E/W.
        """
        if value is None:
            raise ValueError('Empty coordinate')
        s = str(value).strip().upper()
        if not s:
            raise ValueError('Empty coordinate')

        # Try simple float first
        try:
            return float(s)
        except ValueError:
            pass

        # Normalize unicode symbols
        s = (
            s.replace('DEG', '°')
             .replace('º', '°')
             .replace('˚', '°')
             .replace('', "'")
             .replace('′', "'")
             .replace('"', '"')
             .replace('″', '"')
        )

        # Regex for DMS: degrees minutes seconds optional direction
        m = re.match(r"^\s*(\d+(?:\.\d+)?)\D+(\d+(?:\.\d+)?)?\D*(\d+(?:\.\d+)?)?\s*([NSEW])?\s*$", s)
        if not m:
            # Alternative: space separated
            m = re.match(r"^\s*(\d+(?:\.\d+)?)(?:\s+(\d+(?:\.\d+)?))?(?:\s+(\d+(?:\.\d+)?))?\s*([NSEW])?\s*$", s)
        if not m:
            raise ValueError('Invalid coordinate format')

        deg = float(m.group(1))
        minutes = float(m.group(2)) if m.group(2) is not None else 0.0
        seconds = float(m.group(3)) if m.group(3) is not None else 0.0
        direction = m.group(4)

        decimal = deg + (minutes / 60.0) + (seconds / 3600.0)
        if direction in ('S', 'W'):
            decimal = -decimal

        # Validate range
        if is_lat and not (-90.0 <= decimal <= 90.0):
            raise ValueError('Latitude out of range (-90..90)')
        if not is_lat and not (-180.0 <= decimal <= 180.0):
            raise ValueError('Longitude out of range (-180..180)')
        return decimal
    
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        address = request.POST.get('address', '').strip()
        phone = request.POST.get('phone', '').strip()
        email = request.POST.get('email', '').strip()
        lat_raw = request.POST.get('lat')
        lon_raw = request.POST.get('lon')
        radius = request.POST.get('radius') or 150
        
        if name and lat_raw and lon_raw:
            try:
                lat_dd = parse_coordinate(lat_raw, is_lat=True)
                lon_dd = parse_coordinate(lon_raw, is_lat=False)
                
                # Check if name is being changed and if it conflicts with existing company
                if company.name != name:
                    if Company.objects.filter(name=name).exclude(id=company_id).exists():
                        messages.error(request, f'Company with name "{name}" already exists')
                        return render(request, 'admin/edit_company.html', {'company': company})
                
                # Update company
                company.name = name
                company.lat = lat_dd
                company.lon = lon_dd
                company.radius = radius
                company.address = address
                company.phone = phone
                company.email = email
                company.save()
                
                messages.success(request, f'Company {name} updated successfully')
                return redirect('attendance:manage_companies')
            except Exception as e:
                messages.error(request, f'Failed to update company: {str(e)}')
        else:
            messages.error(request, 'Please fill all required fields')
    
    return render(request, 'admin/edit_company.html', {'company': company})


@login_required
@user_passes_test(is_admin)
def delete_company(request, company_id):
    """Delete a company."""
    try:
        company = Company.objects.get(id=company_id)
        company_name = company.name
        company.delete()
        messages.success(request, f'Company {company_name} deleted successfully')
    except Company.DoesNotExist:
        messages.error(request, 'Company not found')
    except Exception as e:
        messages.error(request, f'Failed to delete company: {str(e)}')
    
    return redirect('attendance:manage_companies')


@login_required
@user_passes_test(is_student)
def mark_attendance_view(request):
    """Enhanced student attendance marking view with check-in/check-out support."""
    if request.method == 'POST':
        try:
            student_profile = request.user.student_profile
            today = date.today()
            # Localized current time for accurate display
            current_time = timezone.localtime(timezone.now()).time()
            
            # Get form data directly
            lat = request.POST.get('lat')
            lon = request.POST.get('lon')
            accuracy = request.POST.get('accuracy')
            selfie_image = request.FILES.get('selfie_image')
            device_info = request.POST.get('device_info', '')
            attendance_type = request.POST.get('attendance_type', 'check_in')
            
            # Validate required fields
            if not all([lat, lon, accuracy, selfie_image]):
                messages.error(request, 'All fields are required.')
                context = _build_student_location_context(student_profile)
                return render(request, 'student/mark_attendance.html', context)
            
            # Convert to appropriate types
            try:
                lat = float(lat)
                lon = float(lon)
                accuracy = float(accuracy)
            except (ValueError, TypeError):
                messages.error(request, 'Invalid location or accuracy data.')
                context = _build_student_location_context(student_profile)
                return render(request, 'student/mark_attendance.html', context)
            
            # Geofence verification
            geofence_verified = verify_geofence(student_profile, lat, lon, accuracy)
            
            # Face verification with error handling
            try:
                # Check if student has face enrolled
                if not student_profile.face_enrolled:
                    print(f"Student {student_profile.name} not face enrolled, skipping face verification")
                    face_verified = True  # Allow attendance if not enrolled
                else:
                    # Try face verification
                    face_verified = verify_face(student_profile, selfie_image)
                    print(f"Face verification result for {student_profile.name}: {face_verified}")
            except Exception as e:
                print(f"Face verification failed for {student_profile.name}: {str(e)}")
                # Log the full error for debugging
                import traceback
                print(f"Face verification traceback: {traceback.format_exc()}")
                face_verified = True  # Fallback: allow attendance if face verification fails
            
            # Check existing attendance for today
            existing_attendance = AttendanceLog.objects.filter(
                student=student_profile, 
                date=today
            ).order_by('-updated_at', '-time').first()
            
            if attendance_type == 'check_in':
                # Check if already completed check-in AND check-out
                if existing_attendance and existing_attendance.check_in_time and existing_attendance.check_out_time:
                    messages.error(request, 'You have already completed attendance for today.')
                    context = _build_student_location_context(student_profile)
                    return render(request, 'student/mark_attendance.html', context)
                
                # Create or update attendance log for check-in
                # Allow recording even if verification fails (will be marked as fail_geo/fail_face)
                
                if existing_attendance:
                    # Overwrite the previous attempt for today to reflect the latest result
                    existing_attendance.time = current_time
                    existing_attendance.check_in_time = current_time
                    existing_attendance.lat = lat
                    existing_attendance.lon = lon
                    existing_attendance.accuracy = accuracy
                    existing_attendance.device_info = device_info
                    existing_attendance.geofence_verified = geofence_verified
                    existing_attendance.face_verified = face_verified
                    existing_attendance.save()
                    attendance_log = existing_attendance
                else:
                    attendance_log = AttendanceLog.objects.create(
                        student=student_profile,
                        date=today,
                        time=current_time,
                        attendance_type=attendance_type,
                        lat=lat,
                        lon=lon,
                        accuracy=accuracy,
                        geofence_verified=geofence_verified,
                        face_verified=face_verified,
                        device_info=device_info,
                        check_in_time=current_time
                    )
                
                if geofence_verified and face_verified:
                    messages.success(request, 'Check-in successful! You are now pending check-out to be marked present.')
                else:
                    reasons = []
                    if not geofence_verified: reasons.append("Location mismatch")
                    if not face_verified: reasons.append("Face verification failed")
                    messages.warning(request, f'Check-in recorded but failed: {", ".join(reasons)}. You are marked as Absent/Failed.')
            
            elif attendance_type == 'check_out':
                if not existing_attendance or not existing_attendance.check_in_time:
                    messages.error(request, 'You must check in first before checking out.')
                    context = _build_student_location_context(student_profile)
                    return render(request, 'student/mark_attendance.html', context)
                
                if existing_attendance.check_out_time:
                    messages.error(request, 'You have already checked out today.')
                    context = _build_student_location_context(student_profile)
                    return render(request, 'student/mark_attendance.html', context)
                
                # Update attendance log for check-out regardless of verification status
                if geofence_verified and face_verified:
                    messages.success(request, 'Check-out successful! You are marked present.')
                else:
                    reasons = []
                    if not geofence_verified: reasons.append("Location mismatch")
                    if not face_verified: reasons.append("Face verification failed")
                    messages.warning(request, f'Check-out recorded but failed: {", ".join(reasons)}. Attendance not marked as Present.')

                # Update attendance log for check-out
                existing_attendance.check_out_time = current_time
                existing_attendance.geofence_verified = geofence_verified
                existing_attendance.face_verified = face_verified
                existing_attendance.save()
                attendance_log = existing_attendance
            
            return redirect('users:student_dashboard')
            
        except Exception as e:
            error_msg = f'Error marking attendance: {str(e)}'
            messages.error(request, error_msg)
            print(f"Attendance error for {student_profile.name}: {str(e)}")  # Debug logging
            # Log the full traceback for debugging
            import traceback
            print(f"Full traceback: {traceback.format_exc()}")

    # GET or fallthrough render with location context
    try:
        student_profile = request.user.student_profile
        context = _build_student_location_context(student_profile)
    except Exception:
        context = {}
    return render(request, 'student/mark_attendance.html', context)


# API Views
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def api_mark_attendance(request):
    """Enhanced API endpoint for marking attendance with check-in/check-out support."""
    serializer = AttendanceMarkSerializer(data=request.data, files=request.FILES)
    if serializer.is_valid():
        try:
            student_profile = request.user.student_profile
            today = date.today()
            current_time = timezone.localtime(timezone.now()).time()
            
            # Get validated data
            lat = serializer.validated_data['lat']
            lon = serializer.validated_data['lon']
            accuracy = serializer.validated_data['accuracy']
            selfie_image = serializer.validated_data['selfie_image']
            device_info = serializer.validated_data.get('device_info', '')
            attendance_type = serializer.validated_data.get('attendance_type', 'check_in')
            
            # Geofence verification
            geofence_verified = verify_geofence(student_profile, lat, lon, accuracy)
            
            # Face verification with error handling
            try:
                # Check if student has face enrolled
                if not student_profile.face_enrolled:
                    print(f"Student {student_profile.name} not face enrolled, skipping face verification")
                    face_verified = True  # Allow attendance if not enrolled
                else:
                    # Try face verification
                    face_verified = verify_face(student_profile, selfie_image)
                    print(f"Face verification result for {student_profile.name}: {face_verified}")
            except Exception as e:
                print(f"Face verification failed for {student_profile.name}: {str(e)}")
                # Log the full error for debugging
                import traceback
                print(f"Face verification traceback: {traceback.format_exc()}")
                face_verified = True  # Fallback: allow attendance if face verification fails
            
            # Check existing attendance for today
            existing_attendance = AttendanceLog.objects.filter(
                student=student_profile, 
                date=today
            ).order_by('-updated_at', '-time').first()
            
            if attendance_type == 'check_in':
                # Check if already completed check-in AND check-out
                if existing_attendance and existing_attendance.check_in_time and existing_attendance.check_out_time:
                    return Response({'error': 'You have already completed attendance for today'}, status=status.HTTP_400_BAD_REQUEST)

                # Require both verifications at check-in
                if not (geofence_verified and face_verified):
                    return Response({'error': 'Check-in failed. Location and face must pass.'}, status=status.HTTP_400_BAD_REQUEST)

                # Create or update attendance log for check-in
                if existing_attendance:
                    existing_attendance.time = current_time
                    existing_attendance.check_in_time = current_time
                    existing_attendance.lat = lat
                    existing_attendance.lon = lon
                    existing_attendance.accuracy = accuracy
                    existing_attendance.device_info = device_info
                    existing_attendance.geofence_verified = geofence_verified
                    existing_attendance.face_verified = face_verified
                    existing_attendance.save()
                    attendance_log = existing_attendance
                else:
                    attendance_log = AttendanceLog.objects.create(
                        student=student_profile,
                        date=today,
                        time=current_time,
                        attendance_type=attendance_type,
                        lat=lat,
                        lon=lon,
                        accuracy=accuracy,
                        geofence_verified=geofence_verified,
                        face_verified=face_verified,
                        device_info=device_info,
                        ip_address=request.META.get('REMOTE_ADDR'),
                        check_in_time=current_time
                    )
            
            elif attendance_type == 'check_out':
                if not existing_attendance or not existing_attendance.check_in_time:
                    return Response({'error': 'You must check in first before checking out'}, status=status.HTTP_400_BAD_REQUEST)
                
                if existing_attendance.check_out_time:
                    return Response({'error': 'You have already checked out today'}, status=status.HTTP_400_BAD_REQUEST)
                
                # Require both verifications at check-out
                if not (geofence_verified and face_verified):
                    return Response({'error': 'Check-out failed. Location and face must pass.'}, status=status.HTTP_400_BAD_REQUEST)

                # Update attendance log for check-out
                existing_attendance.check_out_time = current_time
                existing_attendance.geofence_verified = geofence_verified
                existing_attendance.face_verified = face_verified
                existing_attendance.save()
                attendance_log = existing_attendance
            
            return Response({
                'status': 'success',
                'attendance_id': str(attendance_log.id),
                'attendance_type': attendance_type,
                'geofence_verified': geofence_verified,
                'face_verified': face_verified,
                'overall_status': attendance_log.status,
                'check_in_time': attendance_log.check_in_time.isoformat() if attendance_log.check_in_time else None,
                'check_out_time': attendance_log.check_out_time.isoformat() if attendance_log.check_out_time else None,
                'total_time_minutes': attendance_log.total_time_minutes,
                'formatted_total_time': attendance_log.formatted_total_time
            })
            
        except StudentProfile.DoesNotExist:
            return Response({'error': 'Student profile not found'}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def api_attendance_history(request):
    """API endpoint for getting attendance history."""
    try:
        student_profile = request.user.student_profile
        from_date = request.GET.get('from_date')
        to_date = request.GET.get('to_date')
        
        query = AttendanceLog.objects.filter(student=student_profile)
        
        if from_date:
            query = query.filter(date__gte=from_date)
        if to_date:
            query = query.filter(date__lte=to_date)
        
        attendance_logs = query.order_by('-date', '-time')
        serializer = AttendanceLogSerializer(attendance_logs, many=True)
        
        return Response(serializer.data)
        
    except StudentProfile.DoesNotExist:
        return Response({'error': 'Student profile not found'}, status=status.HTTP_400_BAD_REQUEST)


def verify_geofence(student_profile, current_lat, current_lon, accuracy):
    """Verify if student is within geofence.

    Preference order for geofence center/radius:
    1) Matching Company record (authoritative source)
    2) Fallback to StudentProfile.lat/lon and global radius
    This avoids using stale student coordinates after company changes.
    """
    from .models import Company

    # Ensure numeric types
    try:
        current_lat_f = float(current_lat)
        current_lon_f = float(current_lon)
        accuracy_f = float(accuracy)
    except Exception:
        return False

    # Handle unrealistic accuracy values (often indicates GPS issue or default value)
    # If accuracy is extremely high (> 1000m), assume it's a faulty reading and allow
    # the distance check to determine validity
    # This handles cases where GPS returns default/invalid accuracy values
    if accuracy_f > 1000:
        print(f"Unrealistic GPS accuracy detected: {accuracy_f}m - treating as GPS error, allowing geofence check")
        # Set to reasonable default to pass accuracy check
        accuracy_f = 50.0
    
    # Check GPS accuracy threshold - use reasonable threshold for mobile GPS
    # Mobile GPS can have accuracy of 10-100m in normal conditions
    max_accuracy_threshold = 1000.0  # Very lenient threshold (1km)
    if accuracy_f > max_accuracy_threshold:
        print(f"GPS accuracy too low: {accuracy_f}m > {max_accuracy_threshold}m threshold")
        return False

    # Determine geofence center: prefer company center, fallback to student_profile lat/lon
    company_obj = None
    if getattr(student_profile, 'company', None):
        try:
            company_obj = Company.objects.filter(name__iexact=student_profile.company).first()
        except Exception:
            company_obj = None

    if company_obj and company_obj.lat is not None and company_obj.lon is not None:
        center_lat = float(company_obj.lat)
        center_lon = float(company_obj.lon)
    elif student_profile.lat is not None and student_profile.lon is not None:
        center_lat = float(student_profile.lat)
        center_lon = float(student_profile.lon)
    else:
        return False

    # Distance in meters
    distance_m = geodesic((center_lat, center_lon), (current_lat_f, current_lon_f)).meters

    # Radius: prefer company radius if available, else global default
    radius_m = float(company_obj.radius) if company_obj and company_obj.radius is not None else float(settings.GEOFENCE_RADIUS)

    # More lenient geofence check: allow if within radius OR if accuracy uncertainty is large
    # This handles edge cases where student is right at the boundary
    # Factor in GPS accuracy in the decision: if distance is close enough considering accuracy
    effective_radius = radius_m + (accuracy_f / 2)  # Add half of accuracy as buffer
    
    within_radius = distance_m <= radius_m
    within_effective_radius = distance_m <= effective_radius

    # Log geofence verification details for debugging
    print(f"Geofence verification for {student_profile.name}:")
    print(f"  Center: {center_lat}, {center_lon}")
    print(f"  Current: {current_lat_f}, {current_lon_f}")
    print(f"  Distance: {distance_m:.2f}m")
    print(f"  Radius: {radius_m}m")
    print(f"  Effective radius (with accuracy buffer): {effective_radius:.2f}m")
    print(f"  Accuracy: {accuracy_f}m")
    print(f"  Within radius: {within_radius}")
    print(f"  Within effective radius: {within_effective_radius}")

    # Allow geofence if within strict radius OR within effective radius (considering GPS uncertainty)
    return within_radius or within_effective_radius


def _build_student_location_context(student_profile):
    """Prepare context with assigned coordinates and geofence radius for client-side gating.

    Preference order matches verify_geofence: use Company center/radius when available,
    fallback to StudentProfile.lat/lon and global radius. This keeps UI and backend
    consistent and avoids stale student coordinates after company changes.
    """
    from .models import Company
    company_obj = None
    if getattr(student_profile, 'company', None):
        company_obj = Company.objects.filter(name__iexact=student_profile.company).first()

    assigned_lat = None
    assigned_lon = None
    radius_m = float(settings.GEOFENCE_RADIUS)

    if company_obj and company_obj.radius is not None:
        radius_m = float(company_obj.radius)

    # Prefer company center for display as authoritative
    if company_obj and company_obj.lat is not None and company_obj.lon is not None:
        assigned_lat = float(company_obj.lat)
        assigned_lon = float(company_obj.lon)
    elif student_profile.lat is not None and student_profile.lon is not None:
        assigned_lat = float(student_profile.lat)
        assigned_lon = float(student_profile.lon)

    return {
        'assigned_lat': assigned_lat,
        'assigned_lon': assigned_lon,
        'geofence_radius': radius_m,
    }


def verify_face(student_profile, selfie_image):
    """Verify face using local face_recognition library with StudentProfile.face_encoding."""
    # Check if face verification is enabled
    if not settings.FACE_VERIFICATION_ENABLED:
        print(f"Face verification disabled in settings, allowing attendance")
        return True
    
    if not student_profile.face_enrolled:
        print(f"Face verification skipped: Student {student_profile.name} not enrolled")
        return True  # Allow attendance if not enrolled
    
    if not student_profile.face_encoding:
        print(f"Face verification skipped: Student {student_profile.name} has no face encoding data")
        print(f"Note: face_enrolled={student_profile.face_enrolled} but face_encoding is None - data inconsistency")
        # Try to use face service as fallback
        return _verify_face_with_service(student_profile, selfie_image)
    
    # Check if this is a dummy encoding (all zeros)
    if all(x == 0.0 for x in student_profile.face_encoding):
        print(f"Face verification: Student {student_profile.name} has dummy encoding, using basic validation")
        return _basic_image_validation(selfie_image, student_profile)
    
    try:
        # Try to import face_recognition
        import face_recognition
        import numpy as np
        from PIL import Image
        import io
        
        # Get the enrolled face encoding from the database
        enrolled_encoding = np.array(student_profile.face_encoding)
        
        # Read and process the selfie image
        selfie_image.file.seek(0)  # Reset file pointer
        image_data = selfie_image.file.read()
        
        # Load image using PIL from bytes
        pil_image = Image.open(io.BytesIO(image_data))
        
        # Convert PIL image to RGB if necessary
        if pil_image.mode != 'RGB':
            pil_image = pil_image.convert('RGB')
        
        # Convert to numpy array for face_recognition
        face_image = np.array(pil_image)
        
        # Detect face locations in the selfie
        face_locations = face_recognition.face_locations(face_image)
        
        if not face_locations:
            print(f"No face detected in selfie for {student_profile.name}")
            return False
        
        if len(face_locations) > 1:
            print(f"Multiple faces detected in selfie for {student_profile.name}")
            return False
        
        # Generate face encoding for the selfie
        selfie_encodings = face_recognition.face_encodings(face_image, face_locations)
        
        if not selfie_encodings:
            print(f"Could not generate face encoding for {student_profile.name}")
            return False
        
        selfie_encoding = selfie_encodings[0]
        
        # Calculate face distance between enrolled and selfie
        face_distance = face_recognition.face_distance([enrolled_encoding], selfie_encoding)[0]
        
        # Determine match (threshold: 0.6 - same as the external service)
        threshold = 0.6
        matched = face_distance <= threshold
        
        print(f"Face verification for {student_profile.name}: distance={face_distance:.3f}, threshold={threshold}, matched={matched}")
        
        return matched
        
    except ImportError as e:
        print(f"Face recognition library not available: {str(e)}")
        print(f"Falling back to face service for {student_profile.name}")
        # Fallback: Use face service
        return _verify_face_with_service(student_profile, selfie_image)
    except Exception as e:
        print(f"Face verification error for {student_profile.name}: {str(e)}")
        # For any other error, fall back to face service
        return _verify_face_with_service(student_profile, selfie_image)


def _verify_face_with_service(student_profile, selfie_image):
    """Verify face using the face service as fallback."""
    try:
        import requests
        
        # Prepare the image for the face service
        selfie_image.file.seek(0)  # Reset file pointer
        image_data = selfie_image.file.read()
        
        files = {'image': ('selfie.jpg', image_data, 'image/jpeg')}
        data = {'student_id': str(student_profile.id)}
        
        # Call face service verify endpoint
        face_service_url = getattr(settings, 'FACE_SERVICE_URL', 'http://localhost:8001')
        response = requests.post(
            f"{face_service_url}/face/verify",
            files=files,
            data=data,
            timeout=30
        )
        
        if response.status_code == 200:
            result = response.json()
            if result.get('success'):
                matched = result.get('matched', False)
                distance = result.get('distance', 0.0)
                print(f"Face service verification for {student_profile.name}: distance={distance:.3f}, matched={matched}")
                return matched
            else:
                print(f"Face service verification failed for {student_profile.name}: {result.get('message', 'Unknown error')}")
                return False
        else:
            print(f"Face service request failed for {student_profile.name}: {response.status_code}")
            return False
            
    except requests.exceptions.RequestException as e:
        print(f"Could not connect to face service for {student_profile.name}: {str(e)}")
        # Final fallback: basic image validation
        return _basic_image_validation(selfie_image, student_profile)
    except Exception as e:
        print(f"Face service verification error for {student_profile.name}: {str(e)}")
        # Final fallback: basic image validation
        return _basic_image_validation(selfie_image, student_profile)


def _basic_image_validation(selfie_image, student_profile):
    """Basic image validation as fallback when face_recognition is not available."""
    try:
        from PIL import Image
        import io
        
        # Read and process the selfie image
        selfie_image.file.seek(0)  # Reset file pointer
        image_data = selfie_image.file.read()
        
        # Load image using PIL from bytes
        pil_image = Image.open(io.BytesIO(image_data))
        
        # Basic validation: check if image is valid and has reasonable dimensions
        width, height = pil_image.size
        
        # Check minimum dimensions (should be at least 50x50 pixels - more lenient)
        if width < 50 or height < 50:
            print(f"Image too small for {student_profile.name}: {width}x{height}")
            return False
        
        # Check maximum dimensions (should not be too large)
        if width > 5000 or height > 5000:
            print(f"Image too large for {student_profile.name}: {width}x{height}")
            return False
        
        # Check file size (should be reasonable)
        if len(image_data) < 500:  # Less than 500 bytes is suspicious - more lenient
            print(f"Image file too small for {student_profile.name}: {len(image_data)} bytes")
            return False
        
        if len(image_data) > 10 * 1024 * 1024:  # More than 10MB is too large
            print(f"Image file too large for {student_profile.name}: {len(image_data)} bytes")
            return False
        
        print(f"Basic image validation passed for {student_profile.name}: {width}x{height}, {len(image_data)} bytes")
        return True
        
    except Exception as e:
        print(f"Basic image validation failed for {student_profile.name}: {str(e)}")
        # For dummy encodings, be more lenient - allow attendance if image seems reasonable
        try:
            # Try to get basic file info
            selfie_image.file.seek(0)
            image_data = selfie_image.file.read()
            if len(image_data) > 500 and len(image_data) < 10 * 1024 * 1024:
                print(f"Allowing attendance for {student_profile.name} based on file size: {len(image_data)} bytes")
                return True
        except:
            pass
        return False
