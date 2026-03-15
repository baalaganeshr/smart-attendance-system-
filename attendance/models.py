"""
Attendance models for the geofenced attendance system.
"""

import uuid
from django.db import models
from django.conf import settings
from django.utils import timezone


class Company(models.Model):
    """
    Company table that stores geolocation for mapping students by company.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    address = models.TextField(blank=True, help_text="Full address of the company/college")
    phone = models.CharField(max_length=50, blank=True, help_text="Contact phone number")
    email = models.EmailField(blank=True, help_text="Contact email address")
    lat = models.DecimalField(max_digits=9, decimal_places=6, help_text="Center latitude of company")
    lon = models.DecimalField(max_digits=9, decimal_places=6, help_text="Center longitude of company")
    radius = models.DecimalField(max_digits=6, decimal_places=2, default=150.0, help_text="Default geofence radius in meters")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'companies'
        verbose_name = 'Company'
        verbose_name_plural = 'Companies'

    def __str__(self):
        return self.name

class AttendanceLog(models.Model):
    """
    Detailed attendance records with geofencing and face verification.
    Supports check-in/check-out with time tracking.
    """
    STATUS_CHOICES = [
        ('present', 'Present'),
        ('fail_geo', 'Geofence Failed'),
        ('fail_face', 'Face Verification Failed'),
        ('fail_both', 'Both Failed'),
    ]
    
    ATTENDANCE_TYPE_CHOICES = [
        ('check_in', 'Check In'),
        ('check_out', 'Check Out'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    student = models.ForeignKey('users.StudentProfile', on_delete=models.CASCADE, related_name='attendance_logs')
    date = models.DateField()
    time = models.TimeField()
    attendance_type = models.CharField(max_length=10, choices=ATTENDANCE_TYPE_CHOICES, default='check_in')
    lat = models.DecimalField(max_digits=9, decimal_places=6, help_text="Current latitude")
    lon = models.DecimalField(max_digits=9, decimal_places=6, help_text="Current longitude")
    accuracy = models.DecimalField(max_digits=9, decimal_places=2, help_text="GPS accuracy in meters")
    geofence_verified = models.BooleanField(default=False, help_text="Whether geofence check passed")
    face_verified = models.BooleanField(default=False, help_text="Whether face verification passed")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='fail_both')
    device_info = models.TextField(blank=True, help_text="Device information")
    ip_address = models.GenericIPAddressField(blank=True, null=True, help_text="IP address")
    # Time tracking fields
    check_in_time = models.TimeField(null=True, blank=True, help_text="Check-in time")
    check_out_time = models.TimeField(null=True, blank=True, help_text="Check-out time")
    total_time_minutes = models.IntegerField(null=True, blank=True, help_text="Total time spent in minutes")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'attendance_logs'
        verbose_name = 'Attendance Log'
        verbose_name_plural = 'Attendance Logs'
        ordering = ['-date', '-time']
    
    def __str__(self):
        return f"{self.student.name} - {self.date} ({self.get_status_display()})"
    
    def save(self, *args, **kwargs):
        # Auto-determine status based on verification results
        if self.geofence_verified and self.face_verified:
            self.status = 'present'
        elif not self.geofence_verified and not self.face_verified:
            self.status = 'fail_both'
        elif not self.geofence_verified:
            self.status = 'fail_geo'
        else:
            self.status = 'fail_face'
        
        # Calculate total time if both check-in and check-out are present
        if self.check_in_time and self.check_out_time:
            self.calculate_total_time()
        
        super().save(*args, **kwargs)
    
    def calculate_total_time(self):
        """Calculate total time spent between check-in and check-out."""
        if self.check_in_time and self.check_out_time:
            from datetime import datetime, timedelta
            
            # Create datetime objects for calculation
            check_in_dt = datetime.combine(self.date, self.check_in_time)
            check_out_dt = datetime.combine(self.date, self.check_out_time)
            
            # Handle case where check-out is next day
            if check_out_dt < check_in_dt:
                check_out_dt += timedelta(days=1)
            
            time_diff = check_out_dt - check_in_dt
            self.total_time_minutes = int(time_diff.total_seconds() / 60)
    
    @property
    def is_present(self):
        return self.status == 'present'
    
    @property
    def verification_summary(self):
        return {
            'geofence': self.geofence_verified,
            'face': self.face_verified,
            'overall': self.is_present
        }
    
    @property
    def formatted_total_time(self):
        """Return formatted total time string."""
        if self.total_time_minutes:
            hours = self.total_time_minutes // 60
            minutes = self.total_time_minutes % 60
            return f"{hours}h {minutes}m"
        return "N/A"


class GeofenceSettings(models.Model):
    """
    Configurable geofence settings per company/organization.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.CharField(max_length=100, unique=True, help_text="Company/Organization name")
    radius = models.DecimalField(max_digits=6, decimal_places=2, default=30.0, help_text="Geofence radius in meters")
    accuracy_threshold = models.DecimalField(max_digits=5, decimal_places=2, default=25.0, help_text="GPS accuracy threshold in meters")
    center_lat = models.DecimalField(max_digits=9, decimal_places=6, help_text="Center latitude of geofence")
    center_lon = models.DecimalField(max_digits=9, decimal_places=6, help_text="Center longitude of geofence")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'geofence_settings'
        verbose_name = 'Geofence Setting'
        verbose_name_plural = 'Geofence Settings'
    
    def __str__(self):
        return f"{self.company} - {self.radius}m radius"
    
    @property
    def center_coordinates(self):
        return (float(self.center_lat), float(self.center_lon))


class SystemSettings(models.Model):
    """
    Global system settings managed by super admin.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.CharField(max_length=100, unique=True, help_text="Setting key")
    value = models.TextField(help_text="Setting value")
    description = models.TextField(blank=True, help_text="Setting description")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'system_settings'
        verbose_name = 'System Setting'
        verbose_name_plural = 'System Settings'
    
    def __str__(self):
        return f"{self.key}: {self.value}"
    
    @classmethod
    def get_setting(cls, key, default=None):
        """Get a system setting value."""
        try:
            setting = cls.objects.get(key=key, is_active=True)
            return setting.value
        except cls.DoesNotExist:
            return default
    
    @classmethod
    def set_setting(cls, key, value, description=""):
        """Set a system setting value."""
        setting, created = cls.objects.get_or_create(
            key=key,
            defaults={'value': value, 'description': description}
        )
        if not created:
            setting.value = value
            setting.description = description
            setting.save()
        return setting


class ContactMessage(models.Model):
    """
    Contact form messages from students to admins.
    """
    STATUS_CHOICES = [
        ('new', 'New'),
        ('in_progress', 'In Progress'),
        ('resolved', 'Resolved'),
        ('closed', 'Closed'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    student = models.ForeignKey('users.StudentProfile', on_delete=models.CASCADE, related_name='contact_messages')
    subject = models.CharField(max_length=200)
    message = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='new')
    admin_response = models.TextField(blank=True, help_text="Admin's response")
    responded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='admin_responses')
    responded_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'contact_messages'
        verbose_name = 'Contact Message'
        verbose_name_plural = 'Contact Messages'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.student.name}: {self.subject}"
    
    def mark_responded(self, admin_user, response):
        """Mark message as responded to by admin."""
        self.admin_response = response
        self.responded_by = admin_user
        self.responded_at = timezone.now()
        self.status = 'resolved'
        self.save()
