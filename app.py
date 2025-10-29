import os
import dash
from dash import html, dcc, dash_table, Input, Output, State
import plotly.graph_objs as go
import plotly.express as px
from plotly.subplots import make_subplots
import pandas as pd
import datetime
import random
import json
from collections import deque
import numpy as np
from flask import request, jsonify
from flask_cors import CORS
import psycopg2
from psycopg2.extras import RealDictCursor
import threading
import time

# ------------------------------
# Configuration
# ------------------------------
TOTAL_SLOTS = 100
HOURLY_RATE = 50  # Mauritian Rupees per hour
FREE_HOURS = 2
PENALTY_RATE = 100  # Mauritian Rupees per hour after free period
PEAK_HOUR_MULTIPLIER = 1.5  # Dynamic pricing during peak hours
PEAK_HOURS = [(7, 10), (17, 20)]  # 7-10 AM and 5-8 PM

# Database URL from environment variable (set in Render)
DATABASE_URL = os.environ.get('DATABASE_URL', 'postgresql://localhost/parking_system')

# Admin credentials
ADMIN_CREDENTIALS = {
    "admin": "admin123",
    "operator": "operator123"
}

USER_ROLES = {
    "admin": "admin",
    "operator": "operator"
}

# Historical data storage (for charts)
occupancy_history = deque(maxlen=50)
revenue_history = deque(maxlen=50)
timestamp_history = deque(maxlen=50)

# Alert system
alerts = []

# ------------------------------
# Database Functions
# ------------------------------

def get_db_connection():
    """Get database connection"""
    try:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
        return conn
    except Exception as e:
        print(f"Database connection error: {e}")
        return None

def init_database():
    """Initialize database tables"""
    conn = get_db_connection()
    if not conn:
        print("Failed to connect to database")
        return False
    
    try:
        cur = conn.cursor()
        
        # Create parking_slots table
        cur.execute('''
            CREATE TABLE IF NOT EXISTS parking_slots (
                slot_id VARCHAR(10) PRIMARY KEY,
                zone VARCHAR(10) NOT NULL,
                status VARCHAR(20) DEFAULT 'available',
                entry_time TIMESTAMP,
                vehicle_type VARCHAR(20),
                license_plate VARCHAR(20),
                customer_id VARCHAR(20),
                is_reserved BOOLEAN DEFAULT FALSE,
                maintenance BOOLEAN DEFAULT FALSE,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Create bookings table
        cur.execute('''
            CREATE TABLE IF NOT EXISTS bookings (
                id VARCHAR(20) PRIMARY KEY,
                name VARCHAR(100) NOT NULL,
                phone VARCHAR(20) NOT NULL,
                vehicle VARCHAR(20) NOT NULL,
                license VARCHAR(20) NOT NULL,
                slot_id VARCHAR(10),
                zone VARCHAR(10),
                duration INTEGER NOT NULL,
                cost DECIMAL(10,2) NOT NULL,
                status VARCHAR(20) DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                checkout_time TIMESTAMP
            )
        ''')
        
        # Create activity_log table
        cur.execute('''
            CREATE TABLE IF NOT EXISTS activity_log (
                id SERIAL PRIMARY KEY,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                user_name VARCHAR(50),
                action VARCHAR(100),
                details TEXT
            )
        ''')
        
        # Initialize parking slots if empty
        cur.execute('SELECT COUNT(*) FROM parking_slots')
        count = cur.fetchone()['count']
        
        if count == 0:
            print("Initializing parking slots...")
            for i in range(TOTAL_SLOTS):
                slot_id = f"P{str(i + 1).zfill(3)}"
                zone = f"Zone-{chr(65 + i // 25)}"
                cur.execute('''
                    INSERT INTO parking_slots (slot_id, zone, status)
                    VALUES (%s, %s, 'available')
                ''', (slot_id, zone))
            
            # Add some occupied slots for demo
            for i in range(35):
                slot_id = f"P{str(i + 1).zfill(3)}"
                entry_time = datetime.datetime.now() - datetime.timedelta(hours=random.uniform(0, 8))
                vehicle_type = random.choice(["Car", "Bike", "SUV"])
                license_plate = f"MU-{random.randint(1000, 9999)}"
                customer_id = f"C{random.randint(1000, 9999)}"
                
                cur.execute('''
                    UPDATE parking_slots 
                    SET status = 'occupied', entry_time = %s, vehicle_type = %s, 
                        license_plate = %s, customer_id = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE slot_id = %s
                ''', (entry_time, vehicle_type, license_plate, customer_id, slot_id))
        
        conn.commit()
        cur.close()
        conn.close()
        print("Database initialized successfully")
        return True
        
    except Exception as e:
        print(f"Database initialization error: {e}")
        conn.rollback()
        cur.close()
        conn.close()
        return False

def log_activity(action, details, user="System"):
    """Log activity to database"""
    conn = get_db_connection()
    if not conn:
        return
    
    try:
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO activity_log (user_name, action, details)
            VALUES (%s, %s, %s)
        ''', (user, action, details))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Activity log error: {e}")
        if conn:
            conn.rollback()
            cur.close()
            conn.close()

def get_parking_data():
    """Get parking data from database"""
    conn = get_db_connection()
    if not conn:
        return pd.DataFrame()
    
    try:
        query = '''
            SELECT slot_id, zone, status, entry_time, vehicle_type, 
                   license_plate, customer_id, is_reserved, maintenance
            FROM parking_slots 
            ORDER BY slot_id
        '''
        df = pd.read_sql_query(query, conn)
        conn.close()
        
        # Calculate revenue and fines
        rows = []
        for _, row in df.iterrows():
            fine = 0
            revenue = 0
            duration_str = "-"
            duration_hours = 0
            
            status = row['status']
            if row['maintenance']:
                status = "maintenance"
            
            if status == 'occupied' and row['entry_time']:
                entry_dt = pd.to_datetime(row['entry_time'])
                duration_seconds = (datetime.datetime.now() - entry_dt).total_seconds()
                duration_hours = duration_seconds / 3600
                total_hours = int(duration_hours) + (1 if duration_hours % 1 > 0 else 0)
                
                hours = int(duration_hours)
                minutes = int((duration_seconds % 3600) // 60)
                duration_str = f"{hours}h {minutes}m"
                
                current_rate = get_dynamic_rate()
                revenue = total_hours * current_rate
                
                if duration_hours > FREE_HOURS:
                    extra_hours = total_hours - FREE_HOURS
                    fine = extra_hours * PENALTY_RATE
            
            rows.append({
                "Slot ID": row['slot_id'],
                "Zone": row['zone'],
                "Status": status.upper(),
                "Vehicle": row['vehicle_type'] if status == 'occupied' else "-",
                "License": row['license_plate'] if status == 'occupied' else "-",
                "Customer ID": row['customer_id'] if status == 'occupied' else "-",
                "Entry Time": str(row['entry_time']) if row['entry_time'] else "-",
                "Duration": duration_str,
                "Rate": f"Rs {get_dynamic_rate()}/hr" if status == 'occupied' else "-",
                "Parking Fee": f"Rs {revenue}",
                "Overstay Fine": f"Rs {fine}",
                "Total": f"Rs {revenue + fine}",
                "_revenue": revenue,
                "_fine": fine,
                "_duration_hours": duration_hours,
                "_is_reserved": row['is_reserved'],
                "_maintenance": row['maintenance']
            })
        
        return pd.DataFrame(rows)
        
    except Exception as e:
        print(f"Get parking data error: {e}")
        if conn:
            conn.close()
        return pd.DataFrame()

def get_bookings():
    """Get bookings from database"""
    conn = get_db_connection()
    if not conn:
        return []
    
    try:
        cur = conn.cursor()
        cur.execute('''
            SELECT id, name, phone, vehicle, license, slot_id, zone, 
                   duration, cost, status, created_at, checkout_time
            FROM bookings 
            ORDER BY created_at DESC
        ''')
        bookings = cur.fetchall()
        cur.close()
        conn.close()
        
        # Convert to list of dicts
        booking_list = []
        for booking in bookings:
            booking_list.append({
                'id': booking['id'],
                'name': booking['name'],
                'phone': booking['phone'],
                'vehicle': booking['vehicle'],
                'license': booking['license'],
                'slot': booking['slot_id'],
                'zone': booking['zone'],
                'duration': booking['duration'],
                'cost': float(booking['cost']),
                'status': booking['status'],
                'timestamp': booking['created_at'].strftime("%Y-%m-%d %H:%M:%S"),
                'checkout_time': booking['checkout_time'].strftime("%Y-%m-%d %H:%M:%S") if booking['checkout_time'] else None
            })
        
        return booking_list
        
    except Exception as e:
        print(f"Get bookings error: {e}")
        if conn:
            conn.close()
        return []

def create_booking_in_db(booking_data):
    """Create a booking in the database"""
    conn = get_db_connection()
    if not conn:
        return None
    
    try:
        cur = conn.cursor()
        
        # Insert booking
        cur.execute('''
            INSERT INTO bookings (id, name, phone, vehicle, license, slot_id, zone, duration, cost, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (
            booking_data['id'], booking_data['name'], booking_data['phone'],
            booking_data['vehicle'], booking_data['license'], booking_data['slot'],
            booking_data['zone'], booking_data['duration'], booking_data['cost'], 'active'
        ))
        
        # Update slot as reserved
        cur.execute('''
            UPDATE parking_slots 
            SET is_reserved = TRUE, updated_at = CURRENT_TIMESTAMP
            WHERE slot_id = %s
        ''', (booking_data['slot'],))
        
        conn.commit()
        cur.close()
        conn.close()
        return booking_data
        
    except Exception as e:
        print(f"Create booking error: {e}")
        if conn:
            conn.rollback()
            cur.close()
            conn.close()
        return None

def checkout_booking_in_db(booking_id):
    """Checkout a booking in the database"""
    conn = get_db_connection()
    if not conn:
        return None
    
    try:
        cur = conn.cursor()
        
        # Get booking details
        cur.execute('SELECT * FROM bookings WHERE id = %s', (booking_id,))
        booking = cur.fetchone()
        
        if not booking:
            cur.close()
            conn.close()
            return None
        
        # Update booking status
        cur.execute('''
            UPDATE bookings 
            SET status = 'completed', checkout_time = CURRENT_TIMESTAMP
            WHERE id = %s
        ''', (booking_id,))
        
        # Free up the slot
        cur.execute('''
            UPDATE parking_slots 
            SET is_reserved = FALSE, updated_at = CURRENT_TIMESTAMP
            WHERE slot_id = %s
        ''', (booking['slot_id'],))
        
        conn.commit()
        cur.close()
        conn.close()
        
        return {
            'id': booking['id'],
            'name': booking['name'],
            'slot': booking['slot_id'],
            'status': 'completed'
        }
        
    except Exception as e:
        print(f"Checkout booking error: {e}")
        if conn:
            conn.rollback()
            cur.close()
            conn.close()
        return None

# ------------------------------
# Helper Functions
# ------------------------------
def is_peak_hour():
    current_hour = datetime.datetime.now().hour
    for start, end in PEAK_HOURS:
        if start <= current_hour < end:
            return True
    return False

def get_dynamic_rate():
    return HOURLY_RATE * PEAK_HOUR_MULTIPLIER if is_peak_hour() else HOURLY_RATE

def simulate_parking_activity():
    """Simulate some parking activity for demo purposes"""
    conn = get_db_connection()
    if not conn:
        return
    
    try:
        cur = conn.cursor()
        
        # Get a few random slots to update
        cur.execute('''
            SELECT slot_id FROM parking_slots 
            WHERE maintenance = FALSE 
            ORDER BY RANDOM() 
            LIMIT 5
        ''')
        slots = cur.fetchall()
        
        for slot in slots:
            if random.random() < 0.1:  # 10% chance to change
                slot_id = slot['slot_id']
                
                # Get current status
                cur.execute('SELECT status, is_reserved FROM parking_slots WHERE slot_id = %s', (slot_id,))
                current = cur.fetchone()
                
                if current['status'] == 'available' and not current['is_reserved']:
                    # Make occupied
                    entry_time = datetime.datetime.now() - datetime.timedelta(hours=random.uniform(0, 6))
                    vehicle_type = random.choice(["Car", "Bike", "SUV"])
                    license_plate = f"MU-{random.randint(1000, 9999)}"
                    customer_id = f"C{random.randint(1000, 9999)}"
                    
                    cur.execute('''
                        UPDATE parking_slots 
                        SET status = 'occupied', entry_time = %s, vehicle_type = %s,
                            license_plate = %s, customer_id = %s, updated_at = CURRENT_TIMESTAMP
                        WHERE slot_id = %s
                    ''', (entry_time, vehicle_type, license_plate, customer_id, slot_id))
                
                elif current['status'] == 'occupied':
                    # Make available
                    cur.execute('''
                        UPDATE parking_slots 
                        SET status = 'available', entry_time = NULL, vehicle_type = NULL,
                            license_plate = NULL, customer_id = NULL, updated_at = CURRENT_TIMESTAMP
                        WHERE slot_id = %s
                    ''', (slot_id,))
        
        conn.commit()
        cur.close()
        conn.close()
        
    except Exception as e:
        print(f"Simulate activity error: {e}")
        if conn:
            conn.rollback()
            cur.close()
            conn.close()

def predict_occupancy():
    current_hour = datetime.datetime.now().hour
    if 6 <= current_hour < 10:
        return "increasing", "High demand expected - Morning rush"
    elif 16 <= current_hour < 20:
        return "increasing", "High demand expected - Evening rush"
    elif 22 <= current_hour or current_hour < 6:
        return "decreasing", "Low demand expected - Night hours"
    else:
        return "stable", "Normal demand expected"

def check_alerts(df, stats):
    global alerts
    alerts = []

    if stats['occupancy_rate'] > 85:
        alerts.append({
            "type": "critical",
            "icon": "🚨",
            "message": f"Critical: {stats['occupancy_rate']:.1f}% occupancy - Near full capacity!",
            "time": datetime.datetime.now().strftime("%H:%M:%S")
        })
    elif stats['occupancy_rate'] > 70:
        alerts.append({
            "type": "warning",
            "icon": "⚠️",
            "message": f"Warning: {stats['occupancy_rate']:.1f}% occupancy - High demand",
            "time": datetime.datetime.now().strftime("%H:%M:%S")
        })

    if stats['overstay_count'] > 0:
        alerts.append({
            "type": "warning",
            "icon": "⏰",
            "message": f"{stats['overstay_count']} vehicle(s) overstaying - Rs {stats['total_fines']:,.0f} in fines",
            "time": datetime.datetime.now().strftime("%H:%M:%S")
        })

    if stats['total_earnings'] > 10000:
        alerts.append({
            "type": "success",
            "icon": "💰",
            "message": f"Revenue milestone: Rs {stats['total_earnings']:,.0f} earned today",
            "time": datetime.datetime.now().strftime("%H:%M:%S")
        })

    if is_peak_hour():
        alerts.append({
            "type": "info",
            "icon": "📈",
            "message": f"Peak hour pricing active - Rate: Rs {get_dynamic_rate()}/hour",
            "time": datetime.datetime.now().strftime("%H:%M:%S")
        })

def get_statistics(df):
    occupied = len(df[df['Status'] == 'OCCUPIED'])
    available = len(df[df['Status'] == 'AVAILABLE'])
    reserved = len(df[df['_is_reserved'] == True])
    maintenance = len(df[df['_maintenance'] == True])

    occupancy_rate = (occupied / TOTAL_SLOTS) * 100

    total_revenue = df['_revenue'].sum()
    total_fines = df['_fine'].sum()
    total_earnings = total_revenue + total_fines

    occupied_df = df[df['Status'] == 'OCCUPIED']
    avg_duration = occupied_df['_duration_hours'].mean() if len(occupied_df) > 0 else 0

    overstay_count = len(df[df['_fine'] > 0])
    turnover_rate = random.uniform(3, 8)

    if available > 20:
        avg_wait = 0
    else:
        avg_wait = random.uniform(5, 30)

    return {
        'occupied': occupied,
        'available': available,
        'reserved': reserved,
        'maintenance': maintenance,
        'occupancy_rate': occupancy_rate,
        'total_revenue': total_revenue,
        'total_fines': total_fines,
        'total_earnings': total_earnings,
        'avg_duration': avg_duration,
        'overstay_count': overstay_count,
        'turnover_rate': turnover_rate,
        'avg_wait': avg_wait
    }

# ------------------------------
# Initialize Database
# ------------------------------
print("Initializing database...")
if init_database():
    print("Database ready!")
else:
    print("Database initialization failed!")

# ------------------------------
# Dash App Setup
# ------------------------------
app = dash.Dash(__name__, suppress_callback_exceptions=True)
server = app.server
app.title = "Parking Management System"

# Enable CORS for mobile app
CORS(server)

# ============================================
# API ENDPOINTS FOR RORK MOBILE APP
# ============================================

@server.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'success': True,
        'status': 'online',
        'message': 'Parking API is running',
        'database': 'connected' if get_db_connection() else 'disconnected',
        'timestamp': datetime.datetime.now().isoformat()
    })

@server.route('/api/slots', methods=['GET'])
def get_slots_api():
    """Get available slots and statistics"""
    try:
        df = get_parking_data()
        stats = get_statistics(df)
        
        return jsonify({
            'success': True,
            'data': {
                'total': TOTAL_SLOTS,
                'available': stats['available'],
                'occupied': stats['occupied'],
                'occupancy_rate': round(stats['occupancy_rate'], 1),
                'current_rate': get_dynamic_rate(),
                'total_earnings': round(stats['total_earnings'], 2)
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@server.route('/api/zones', methods=['GET'])
def get_zones_api():
    """Get zones availability"""
    try:
        df = get_parking_data()
        zones_data = []
        
        for zone in ['Zone-A', 'Zone-B', 'Zone-C', 'Zone-D']:
            zone_df = df[df['Zone'] == zone]
            available = len(zone_df[zone_df['Status'] == 'AVAILABLE'])
            total = len(zone_df)
            
            zones_data.append({
                'zone': zone,
                'available': available,
                'total': total,
                'percentage': round((available / total) * 100, 1) if total > 0 else 0
            })
        
        return jsonify({
            'success': True,
            'zones': zones_data
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@server.route('/api/booking', methods=['POST'])
def create_booking_api():
    """Create a new booking via API"""
    try:
        data = request.json
        
        # Validate input
        required_fields = ['name', 'phone', 'vehicle', 'license', 'duration']
        if not all(key in data for key in required_fields):
            return jsonify({
                'success': False, 
                'error': 'Missing required fields. Need: ' + ', '.join(required_fields)
            }), 400
        
        # Find available slot
        df = get_parking_data()
        available_df = df[df['Status'] == 'AVAILABLE']
        
        if len(available_df) == 0:
            return jsonify({'success': False, 'error': 'No slots available'}), 400
        
        # Get slot based on zone preference or first available
        zone = data.get('zone', None)
        if zone:
            zone_available = available_df[available_df['Zone'] == zone]
            slot = zone_available.iloc[0]['Slot ID'] if len(zone_available) > 0 else available_df.iloc[0]['Slot ID']
            selected_zone = zone if len(zone_available) > 0 else available_df.iloc[0]['Zone']
        else:
            slot = available_df.iloc[0]['Slot ID']
            selected_zone = available_df.iloc[0]['Zone']
        
        # Calculate cost
        duration = int(data['duration'])
        cost = duration * get_dynamic_rate()
        
        # Generate booking ID
        timestamp = datetime.datetime.now().strftime("%m%d%H%M")
        booking_id = f"BK{timestamp}"
        
        booking = {
            'id': booking_id,
            'name': data['name'],
            'phone': data['phone'],
            'vehicle': data['vehicle'],
            'license': data['license'],
            'slot': slot,
            'zone': selected_zone,
            'duration': duration,
            'cost': round(cost, 2)
        }
        
        # Save to database
        result = create_booking_in_db(booking)
        if not result:
            return jsonify({'success': False, 'error': 'Failed to create booking'}), 500
        
        # Log activity
        log_activity("Booking Created", f"Booking {booking_id} for slot {slot}", data['name'])
        
        return jsonify({
            'success': True,
            'booking': result,
            'message': 'Booking created successfully'
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@server.route('/api/bookings', methods=['GET'])
def get_bookings_api():
    """Get all bookings"""
    try:
        status_filter = request.args.get('status', None)
        bookings = get_bookings()
        
        if status_filter:
            bookings = [b for b in bookings if b['status'] == status_filter]
        
        return jsonify({
            'success': True,
            'bookings': bookings,
            'count': len(bookings)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@server.route('/api/booking/<booking_id>', methods=['GET'])
def get_booking_api(booking_id):
    """Get a specific booking"""
    try:
        bookings = get_bookings()
        booking = next((b for b in bookings if b['id'] == booking_id), None)
        
        if not booking:
            return jsonify({'success': False, 'error': 'Booking not found'}), 404
        
        return jsonify({
            'success': True,
            'booking': booking
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@server.route('/api/booking/<booking_id>/checkout', methods=['POST'])
def checkout_booking_api(booking_id):
    """Check out a booking"""
    try:
        result = checkout_booking_in_db(booking_id)
        
        if not result:
            return jsonify({'success': False, 'error': 'Booking not found or checkout failed'}), 404
        
        log_activity("Booking Completed", f"Booking {booking_id} checked out", result['name'])
        
        return jsonify({
            'success': True,
            'message': 'Checked out successfully',
            'booking': result
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@server.route('/api/stats', methods=['GET'])
def get_stats_api():
    """Get detailed statistics"""
    try:
        df = get_parking_data()
        stats = get_statistics(df)
        bookings = get_bookings()
        
        # Zone-wise breakdown
        zone_stats = []
        for zone in ['Zone-A', 'Zone-B', 'Zone-C', 'Zone-D']:
            zone_df = df[df['Zone'] == zone]
            zone_stats.append({
                'zone': zone,
                'available': len(zone_df[zone_df['Status'] == 'AVAILABLE']),
                'occupied': len(zone_df[zone_df['Status'] == 'OCCUPIED']),
                'total': len(zone_df)
            })
        
        return jsonify({
            'success': True,
            'stats': {
                'overall': stats,
                'zones': zone_stats,
                'active_bookings': len([b for b in bookings if b['status'] == 'active']),
                'total_bookings': len(bookings),
                'current_rate': get_dynamic_rate()
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# [Keep all the existing UI code - login page, public booking, admin dashboard, etc.]
# This includes all the styling, layout functions, and callbacks
# [The rest of the code remains exactly the same as before]

# Keep all original styling variables
MAIN_BG = '#0f172a'
CARD_BG = '#1e293b'
ACCENT_COLOR = '#3b82f6'
SUCCESS_COLOR = '#10b981'
WARNING_COLOR = '#f59e0b'
DANGER_COLOR = '#ef4444'
INFO_COLOR = '#06b6d4'
TEXT_PRIMARY = '#f1f5f9'
TEXT_SECONDARY = '#94a3b8'

METRIC_CARD = {
    'backgroundColor': CARD_BG,
    'padding': '20px',
    'borderRadius': '12px',
    'boxShadow': '0 4px 6px -1px rgba(0, 0, 0, 0.3)',
    'border': '1px solid #334155',
    'height': '120px',
    'display': 'flex',
    'flexDirection': 'column',
    'justifyContent': 'space-between'
}

CHART_CARD = {
    'backgroundColor': CARD_BG,
    'padding': '20px',
    'borderRadius': '12px',
    'boxShadow': '0 4px 6px -1px rgba(0, 0, 0, 0.3)',
    'border': '1px solid #334155',
    'marginBottom': '20px'
}

BUTTON_STYLE = {
    'backgroundColor': ACCENT_COLOR,
    'color': 'white',
    'border': 'none',
    'padding': '10px 20px',
    'borderRadius': '6px',
    'cursor': 'pointer',
    'fontWeight': '600',
    'fontSize': '14px',
    'marginRight': '10px'
}

# Layout and all callback functions remain the same
app.layout = html.Div([
    dcc.Location(id='url', refresh=False),
    dcc.Store(id='session-store', storage_type='session'),
    html.Div(id='page-content'),
    dcc.Interval(id='interval-component', interval=30 * 1000, n_intervals=0)
], style={'backgroundColor': MAIN_BG, 'minHeight': '100vh'})

# [Include all the existing callback functions and UI rendering functions]
# This is getting long, so I'll continue in the next part...

# Run the app
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
