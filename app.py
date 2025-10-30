import os
import dash
from dash import html, dcc, dash_table, Input, Output, State
import plotly.graph_objs as go
import pandas as pd
import datetime
import random
from collections import deque
from flask import request, jsonify
from flask_cors import CORS
import psycopg2
from psycopg2.extras import RealDictCursor

# Configuration
TOTAL_SLOTS = 100
HOURLY_RATE = 50
FREE_HOURS = 2
PENALTY_RATE = 100
PEAK_HOUR_MULTIPLIER = 1.5
PEAK_HOURS = [(7, 10), (17, 20)]

DATABASE_URL = os.environ.get('DATABASE_URL')
if not DATABASE_URL:
    print("WARNING: DATABASE_URL not set")
    DATABASE_URL = 'postgresql://postgres:postgres@localhost:5432/parking_system'

ADMIN_CREDENTIALS = {"admin": "admin123", "operator": "operator123"}
USER_ROLES = {"admin": "admin", "operator": "operator"}

occupancy_history = deque(maxlen=50)
revenue_history = deque(maxlen=50)
timestamp_history = deque(maxlen=50)
alerts = []


# Database Functions
def get_db_connection():
    try:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
        return conn
    except Exception as e:
        print(f"Database connection error: {e}")
        return None


def init_database():
    conn = get_db_connection()
    if not conn:
        print("Failed to connect to database")
        return False

    try:
        cur = conn.cursor()

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

        cur.execute('''
            CREATE TABLE IF NOT EXISTS activity_log (
                id SERIAL PRIMARY KEY,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                user_name VARCHAR(50),
                action VARCHAR(100),
                details TEXT
            )
        ''')

        cur.execute('SELECT COUNT(*) FROM parking_slots')
        count = cur.fetchone()['count']

        if count == 0:
            print("Initializing parking slots...")
            for i in range(TOTAL_SLOTS):
                slot_id = f"P{str(i + 1).zfill(3)}"
                zone = f"Zone-{chr(65 + i // 25)}"
                cur.execute('INSERT INTO parking_slots (slot_id, zone, status) VALUES (%s, %s, %s)',
                            (slot_id, zone, 'available'))

            for i in range(35):
                slot_id = f"P{str(i + 1).zfill(3)}"
                entry_time = datetime.datetime.now() - datetime.timedelta(hours=random.uniform(0, 8))
                vehicle_type = random.choice(["Car", "Bike", "SUV"])
                license_plate = f"MU-{random.randint(1000, 9999)}"
                customer_id = f"C{random.randint(1000, 9999)}"

                cur.execute('''UPDATE parking_slots SET status = 'occupied', entry_time = %s, 
                            vehicle_type = %s, license_plate = %s, customer_id = %s, updated_at = CURRENT_TIMESTAMP
                            WHERE slot_id = %s''',
                            (entry_time, vehicle_type, license_plate, customer_id, slot_id))

        conn.commit()
        cur.close()
        conn.close()
        print("Database initialized successfully")
        return True

    except Exception as e:
        print(f"Database initialization error: {e}")
        if conn:
            conn.rollback()
            cur.close()
            conn.close()
        return False


def log_activity(action, details, user="System"):
    conn = get_db_connection()
    if not conn:
        return
    try:
        cur = conn.cursor()
        cur.execute('INSERT INTO activity_log (user_name, action, details) VALUES (%s, %s, %s)',
                    (user, action, details))
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
    conn = get_db_connection()
    if not conn:
        return pd.DataFrame()

    try:
        query = '''SELECT slot_id, zone, status, entry_time, vehicle_type, 
                   license_plate, customer_id, is_reserved, maintenance
                   FROM parking_slots ORDER BY slot_id'''
        df = pd.read_sql_query(query, conn)
        conn.close()

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
                "Parking Fee": f"Rs {revenue:.0f}",
                "Overstay Fine": f"Rs {fine:.0f}",
                "Total": f"Rs {(revenue + fine):.0f}",
                "_revenue": revenue,
                "_fine": fine,
                "_duration_hours": duration_hours,
                "_is_reserved": row['is_reserved'],
                "_maintenance": row['maintenance']
            })

        return pd.DataFrame(rows)

    except Exception as e:
        print(f"Get parking data error: {e}")
        return pd.DataFrame()


def get_bookings():
    conn = get_db_connection()
    if not conn:
        return []
    try:
        cur = conn.cursor()
        cur.execute('SELECT * FROM bookings ORDER BY created_at DESC')
        bookings = cur.fetchall()
        cur.close()
        conn.close()

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
                'checkout_time': booking['checkout_time'].strftime("%Y-%m-%d %H:%M:%S") if booking[
                    'checkout_time'] else None
            })
        return booking_list
    except Exception as e:
        print(f"Get bookings error: {e}")
        return []


def create_booking_in_db(booking_data):
    conn = get_db_connection()
    if not conn:
        return None
    try:
        cur = conn.cursor()
        cur.execute('''INSERT INTO bookings (id, name, phone, vehicle, license, slot_id, zone, duration, cost, status)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)''',
                    (booking_data['id'], booking_data['name'], booking_data['phone'],
                     booking_data['vehicle'], booking_data['license'], booking_data['slot'],
                     booking_data['zone'], booking_data['duration'], booking_data['cost'], 'active'))

        cur.execute('UPDATE parking_slots SET is_reserved = TRUE, updated_at = CURRENT_TIMESTAMP WHERE slot_id = %s',
                    (booking_data['slot'],))
        conn.commit()
        cur.close()
        conn.close()
        return booking_data
    except Exception as e:
        print(f"Create booking error: {e}")
        if conn:
            conn.rollback()
        return None


def get_activity_log():
    conn = get_db_connection()
    if not conn:
        return []
    try:
        cur = conn.cursor()
        cur.execute('SELECT * FROM activity_log ORDER BY timestamp DESC LIMIT 50')
        logs = cur.fetchall()
        cur.close()
        conn.close()
        return [dict(log) for log in logs]
    except:
        return []


# Helper Functions
def is_peak_hour():
    current_hour = datetime.datetime.now().hour
    for start, end in PEAK_HOURS:
        if start <= current_hour < end:
            return True
    return False


def get_dynamic_rate():
    return HOURLY_RATE * PEAK_HOUR_MULTIPLIER if is_peak_hour() else HOURLY_RATE


def get_statistics(df):
    if df.empty:
        return {
            'occupied': 0, 'available': 0, 'reserved': 0, 'maintenance': 0,
            'occupancy_rate': 0, 'total_revenue': 0, 'total_fines': 0, 'total_earnings': 0,
            'avg_duration': 0, 'overstay_count': 0, 'turnover_rate': 0, 'avg_wait': 0
        }

    occupied = len(df[df['Status'] == 'OCCUPIED'])
    available = len(df[df['Status'] == 'AVAILABLE'])
    reserved = len(df[df['_is_reserved'] == True])
    maintenance = len(df[df['_maintenance'] == True])
    occupancy_rate = (occupied / TOTAL_SLOTS) * 100 if TOTAL_SLOTS > 0 else 0
    total_revenue = df['_revenue'].sum()
    total_fines = df['_fine'].sum()
    total_earnings = total_revenue + total_fines
    occupied_df = df[df['Status'] == 'OCCUPIED']
    avg_duration = occupied_df['_duration_hours'].mean() if len(occupied_df) > 0 else 0
    overstay_count = len(df[df['_fine'] > 0])
    turnover_rate = random.uniform(3, 8)
    avg_wait = 0 if available > 20 else random.uniform(5, 30)

    return {
        'occupied': occupied, 'available': available, 'reserved': reserved, 'maintenance': maintenance,
        'occupancy_rate': occupancy_rate, 'total_revenue': total_revenue, 'total_fines': total_fines,
        'total_earnings': total_earnings, 'avg_duration': avg_duration, 'overstay_count': overstay_count,
        'turnover_rate': turnover_rate, 'avg_wait': avg_wait
    }


def check_alerts(df, stats):
    global alerts
    alerts = []
    if stats['occupancy_rate'] > 85:
        alerts.append({"type": "critical", "icon": "🚨",
                       "message": f"Critical: {stats['occupancy_rate']:.1f}% occupancy - Near full!",
                       "time": datetime.datetime.now().strftime("%H:%M:%S")})
    elif stats['occupancy_rate'] > 70:
        alerts.append({"type": "warning", "icon": "⚠️",
                       "message": f"Warning: {stats['occupancy_rate']:.1f}% occupancy - High demand",
                       "time": datetime.datetime.now().strftime("%H:%M:%S")})

    if stats['overstay_count'] > 0:
        alerts.append({"type": "warning", "icon": "⏰",
                       "message": f"{stats['overstay_count']} vehicle(s) overstaying - Rs {stats['total_fines']:,.0f} in fines",
                       "time": datetime.datetime.now().strftime("%H:%M:%S")})

    if stats['total_earnings'] > 10000:
        alerts.append({"type": "success", "icon": "💰",
                       "message": f"Revenue milestone: Rs {stats['total_earnings']:,.0f} earned",
                       "time": datetime.datetime.now().strftime("%H:%M:%S")})

    if is_peak_hour():
        alerts.append({"type": "info", "icon": "📈",
                       "message": f"Peak hour pricing active - Rate: Rs {get_dynamic_rate()}/hour",
                       "time": datetime.datetime.now().strftime("%H:%M:%S")})


# Initialize Database
print("Initializing database...")
if init_database():
    print("Database ready!")
else:
    print("Database initialization failed!")

# Dash App Setup
app = dash.Dash(__name__, suppress_callback_exceptions=True)
server = app.server
app.title = "Parking Management System"
CORS(server)


# API ENDPOINTS
@server.route('/api/health', methods=['GET'])
def health_check():
    return jsonify({
        'success': True, 'status': 'online', 'message': 'Parking API is running',
        'database': 'connected' if get_db_connection() else 'disconnected',
        'timestamp': datetime.datetime.now().isoformat()
    })


@server.route('/api/slots', methods=['GET'])
def get_slots_api():
    try:
        df = get_parking_data()
        stats = get_statistics(df)
        return jsonify({
            'success': True,
            'data': {
                'total': TOTAL_SLOTS, 'available': stats['available'], 'occupied': stats['occupied'],
                'occupancy_rate': round(stats['occupancy_rate'], 1), 'current_rate': get_dynamic_rate(),
                'total_earnings': round(stats['total_earnings'], 2)
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@server.route('/api/booking', methods=['POST'])
def create_booking_api():
    try:
        data = request.json
        required_fields = ['name', 'phone', 'vehicle', 'license', 'duration']
        if not all(key in data for key in required_fields):
            return jsonify({'success': False, 'error': 'Missing required fields'}), 400

        df = get_parking_data()
        available_df = df[df['Status'] == 'AVAILABLE']
        if len(available_df) == 0:
            return jsonify({'success': False, 'error': 'No slots available'}), 400

        slot = available_df.iloc[0]['Slot ID']
        selected_zone = available_df.iloc[0]['Zone']
        duration = int(data['duration'])
        cost = duration * get_dynamic_rate()

        booking_id = f"BK{datetime.datetime.now().strftime('%m%d%H%M%S')}"
        booking = {
            'id': booking_id, 'name': data['name'], 'phone': data['phone'],
            'vehicle': data['vehicle'], 'license': data['license'], 'slot': slot,
            'zone': selected_zone, 'duration': duration, 'cost': round(cost, 2)
        }

        result = create_booking_in_db(booking)
        if not result:
            return jsonify({'success': False, 'error': 'Failed to create booking'}), 500

        log_activity("Booking Created", f"Booking {booking_id} for slot {slot}", data['name'])
        return jsonify({'success': True, 'booking': result, 'message': 'Booking created successfully'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@server.route('/api/bookings', methods=['GET'])
def get_bookings_api():
    try:
        bookings = get_bookings()
        return jsonify({'success': True, 'bookings': bookings, 'count': len(bookings)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# Styling
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
    'backgroundColor': CARD_BG, 'padding': '20px', 'borderRadius': '12px',
    'boxShadow': '0 4px 6px -1px rgba(0, 0, 0, 0.3)', 'border': '1px solid #334155',
    'height': '120px', 'display': 'flex', 'flexDirection': 'column', 'justifyContent': 'space-between'
}

CHART_CARD = {
    'backgroundColor': CARD_BG, 'padding': '20px', 'borderRadius': '12px',
    'boxShadow': '0 4px 6px -1px rgba(0, 0, 0, 0.3)', 'border': '1px solid #334155', 'marginBottom': '20px'
}


# Layouts
def login_layout():
    return html.Div([
        html.Div([
            html.H1("🚗 Parking Management System",
                    style={'color': TEXT_PRIMARY, 'marginBottom': '30px', 'textAlign': 'center'}),
            html.Div([
                dcc.Input(id='username-input', type='text', placeholder='Username',
                          style={'width': '100%', 'padding': '12px', 'marginBottom': '15px',
                                 'borderRadius': '6px', 'border': '1px solid #334155', 'backgroundColor': CARD_BG,
                                 'color': TEXT_PRIMARY}),
                dcc.Input(id='password-input', type='password', placeholder='Password',
                          style={'width': '100%', 'padding': '12px', 'marginBottom': '20px',
                                 'borderRadius': '6px', 'border': '1px solid #334155', 'backgroundColor': CARD_BG,
                                 'color': TEXT_PRIMARY}),
                html.Button('Admin Login', id='login-button', n_clicks=0,
                            style={'width': '100%', 'padding': '12px', 'backgroundColor': ACCENT_COLOR,
                                   'color': 'white', 'border': 'none', 'borderRadius': '6px', 'cursor': 'pointer',
                                   'marginBottom': '10px'}),
                html.Button('Public Booking', id='public-booking-button', n_clicks=0,
                            style={'width': '100%', 'padding': '12px', 'backgroundColor': SUCCESS_COLOR,
                                   'color': 'white', 'border': 'none', 'borderRadius': '6px', 'cursor': 'pointer'}),
                html.Div(id='login-alert', style={'marginTop': '15px', 'color': DANGER_COLOR})
            ])
        ], style={'maxWidth': '400px', 'margin': '100px auto', 'padding': '40px',
                  'backgroundColor': CARD_BG, 'borderRadius': '12px', 'boxShadow': '0 10px 25px rgba(0,0,0,0.3)'})
    ], style={'backgroundColor': MAIN_BG, 'minHeight': '100vh'})


def public_booking_layout():
    return html.Div([
        html.Div([
            html.H1("🅿️ Book Your Parking Spot", style={'color': TEXT_PRIMARY, 'textAlign': 'center'}),
            html.Button('← Back to Login', id='back-to-login', n_clicks=0,
                        style={'padding': '8px 16px', 'backgroundColor': TEXT_SECONDARY,
                               'color': 'white', 'border': 'none', 'borderRadius': '6px', 'cursor': 'pointer'})
        ], style={'display': 'flex', 'justifyContent': 'space-between', 'alignItems': 'center',
                  'padding': '20px', 'backgroundColor': CARD_BG, 'marginBottom': '20px'}),

        html.Div([
            html.Div([
                html.H3("Quick Stats", style={'color': TEXT_PRIMARY, 'marginBottom': '20px'}),
                html.Div(id='public-stats', style={'marginBottom': '20px'}),

                html.H3("Book Now", style={'color': TEXT_PRIMARY, 'marginBottom': '20px'}),
                dcc.Input(id='booking-name', type='text', placeholder='Your Name',
                          style={'width': '100%', 'padding': '12px', 'marginBottom': '10px',
                                 'borderRadius': '6px', 'border': '1px solid #334155', 'backgroundColor': CARD_BG,
                                 'color': TEXT_PRIMARY}),
                dcc.Input(id='booking-phone', type='text', placeholder='Phone Number',
                          style={'width': '100%', 'padding': '12px', 'marginBottom': '10px',
                                 'borderRadius': '6px', 'border': '1px solid #334155', 'backgroundColor': CARD_BG,
                                 'color': TEXT_PRIMARY}),
                dcc.Dropdown(id='booking-vehicle',
                             options=[{'label': 'Car', 'value': 'Car'}, {'label': 'Bike', 'value': 'Bike'},
                                      {'label': 'SUV', 'value': 'SUV'}],
                             placeholder='Vehicle Type', style={'marginBottom': '10px'}),
                dcc.Input(id='booking-license', type='text', placeholder='License Plate (e.g., MU-1234)',
                          style={'width': '100%', 'padding': '12px', 'marginBottom': '10px',
                                 'borderRadius': '6px', 'border': '1px solid #334155', 'backgroundColor': CARD_BG,
                                 'color': TEXT_PRIMARY}),
                dcc.Input(id='booking-duration', type='number', placeholder='Duration (hours)', min=1, max=24, value=2,
                          style={'width': '100%', 'padding': '12px', 'marginBottom': '20px',
                                 'borderRadius': '6px', 'border': '1px solid #334155', 'backgroundColor': CARD_BG,
                                 'color': TEXT_PRIMARY}),
                html.Div(id='booking-cost-display', style={'color': TEXT_SECONDARY, 'marginBottom': '15px'}),
                html.Button('Book Slot', id='submit-booking', n_clicks=0,
                            style={'width': '100%', 'padding': '12px', 'backgroundColor': SUCCESS_COLOR,
                                   'color': 'white', 'border': 'none', 'borderRadius': '6px', 'cursor': 'pointer'}),
                html.Div(id='booking-result', style={'marginTop': '15px'})
            ], style={'backgroundColor': CARD_BG, 'padding': '30px', 'borderRadius': '12px'})
        ], style={'maxWidth': '500px', 'margin': '0 auto', 'padding': '20px'})
    ], style={'backgroundColor': MAIN_BG, 'minHeight': '100vh'})


def admin_dashboard_layout():
    return html.Div([
        html.Div([
            html.Div([
                html.H1("🚗 Parking Dashboard", style={'color': TEXT_PRIMARY, 'margin': 0}),
                html.P(f"Current Rate: Rs {get_dynamic_rate()}/hr", style={'color': TEXT_SECONDARY, 'margin': 0})
            ]),
            html.Div([
                html.Button('📊 Dashboard', id='nav-dashboard', n_clicks=0,
                            style={'padding': '8px 16px', 'backgroundColor': ACCENT_COLOR, 'color': 'white',
                                   'border': 'none', 'borderRadius': '6px', 'cursor': 'pointer',
                                   'marginRight': '10px'}),
                html.Button('📝 Bookings', id='nav-bookings', n_clicks=0,
                            style={'padding': '8px 16px', 'backgroundColor': TEXT_SECONDARY, 'color': 'white',
                                   'border': 'none', 'borderRadius': '6px', 'cursor': 'pointer',
                                   'marginRight': '10px'}),
                html.Button('🔍 Activity', id='nav-activity', n_clicks=0,
                            style={'padding': '8px 16px', 'backgroundColor': TEXT_SECONDARY, 'color': 'white',
                                   'border': 'none', 'borderRadius': '6px', 'cursor': 'pointer',
                                   'marginRight': '10px'}),
                html.Button('Logout', id='logout-button', n_clicks=0,
                            style={'padding': '8px 16px', 'backgroundColor': DANGER_COLOR, 'color': 'white',
                                   'border': 'none', 'borderRadius': '6px', 'cursor': 'pointer'})
            ])
        ], style={'display': 'flex', 'justifyContent': 'space-between', 'alignItems': 'center',
                  'padding': '20px', 'backgroundColor': CARD_BG, 'marginBottom': '20px'}),
        html.Div(id='admin-content', style={'padding': '0 20px'})
    ], style={'backgroundColor': MAIN_BG, 'minHeight': '100vh'})


def render_dashboard_content(df, stats):
    occupancy_history.append(stats['occupancy_rate'])
    revenue_history.append(stats['total_earnings'])
    timestamp_history.append(datetime.datetime.now().strftime("%H:%M:%S"))
    check_alerts(df, stats)

    zone_data = []
    for zone in ['Zone-A', 'Zone-B', 'Zone-C', 'Zone-D']:
        zone_df = df[df['Zone'] == zone]
        available = len(zone_df[zone_df['Status'] == 'AVAILABLE'])
        total = len(zone_df)
        zone_data.append({'zone': zone, 'available': available, 'total': total,
                          'percentage': (available / total * 100) if total > 0 else 0})

    return html.Div([
        html.Div([
            html.Div([
                html.Div([
                    html.Span(alert['icon'], style={'fontSize': '24px', 'marginRight': '10px'}),
                    html.Span(alert['message'], style={'color': TEXT_PRIMARY}),
                    html.Span(alert['time'], style={'color': TEXT_SECONDARY, 'fontSize': '12px', 'marginLeft': '10px'})
                ], style={'padding': '15px', 'backgroundColor': CARD_BG, 'borderRadius': '8px',
                          'marginBottom': '10px', 'display': 'flex', 'alignItems': 'center'})
            ]) for alert in alerts
        ], style={'marginBottom': '20px'}),

        html.Div([
            html.Div([html.Div([
                html.H3(f"{stats['available']}", style={'color': SUCCESS_COLOR, 'margin': 0, 'fontSize': '32px'}),
                html.P("Available Slots", style={'color': TEXT_SECONDARY, 'margin': 0})
            ], style=METRIC_CARD)], style={'flex': 1, 'marginRight': '10px'}),

            html.Div([html.Div([
                html.H3(f"{stats['occupied']}", style={'color': WARNING_COLOR, 'margin': 0, 'fontSize': '32px'}),
                html.P("Occupied Slots", style={'color': TEXT_SECONDARY, 'margin': 0})
            ], style=METRIC_CARD)], style={'flex': 1, 'marginRight': '10px'}),

            html.Div([html.Div([
                html.H3(f"{stats['occupancy_rate']:.1f}%",
                        style={'color': ACCENT_COLOR, 'margin': 0, 'fontSize': '32px'}),
                html.P("Occupancy Rate", style={'color': TEXT_SECONDARY, 'margin': 0})
            ], style=METRIC_CARD)], style={'flex': 1, 'marginRight': '10px'}),

            html.Div([html.Div([
                html.H3(f"Rs {stats['total_earnings']:,.0f}",
                        style={'color': SUCCESS_COLOR, 'margin': 0, 'fontSize': '28px'}),
                html.P("Total Revenue", style={'color': TEXT_SECONDARY, 'margin': 0})
            ], style=METRIC_CARD)], style={'flex': 1})
        ], style={'display': 'flex', 'marginBottom': '20px'}),

        html.Div([
            html.Div([
                html.H3("Occupancy Trend", style={'color': TEXT_PRIMARY, 'marginBottom': '15px'}),
                dcc.Graph(figure=go.Figure(
                    data=[go.Scatter(x=list(timestamp_history), y=list(occupancy_history), mode='lines+markers',
                                     line=dict(color=ACCENT_COLOR, width=2), marker=dict(size=6))],
                    layout=go.Layout(plot_bgcolor=CARD_BG, paper_bgcolor=CARD_BG, font=dict(color=TEXT_PRIMARY),
                                     height=250,
                                     xaxis=dict(showgrid=False, title='Time'),
                                     yaxis=dict(showgrid=True, gridcolor='#334155', title='Occupancy %'),
                                     margin=dict(l=40, r=20, t=20, b=40))
                ), config={'displayModeBar': False})
            ], style=CHART_CARD),

            html.Div([
                html.H3("Revenue Trend", style={'color': TEXT_PRIMARY, 'marginBottom': '15px'}),
                dcc.Graph(figure=go.Figure(
                    data=[go.Bar(x=list(timestamp_history), y=list(revenue_history), marker=dict(color=SUCCESS_COLOR))],
                    layout=go.Layout(plot_bgcolor=CARD_BG, paper_bgcolor=CARD_BG, font=dict(color=TEXT_PRIMARY),
                                     height=250,
                                     xaxis=dict(showgrid=False, title='Time'),
                                     yaxis=dict(showgrid=True, gridcolor='#334155', title='Revenue (Rs)'),
                                     margin=dict(l=40, r=20, t=20, b=40))
                ), config={'displayModeBar': False})
            ], style=CHART_CARD)
        ], style={'display': 'grid', 'gridTemplateColumns': '1fr 1fr', 'gap': '20px', 'marginBottom': '20px'}),

        html.Div([
            html.H3("Zone Availability", style={'color': TEXT_PRIMARY, 'marginBottom': '15px'}),
            html.Div([
                html.Div([
                    html.H4(z['zone'], style={'color': TEXT_PRIMARY, 'margin': 0}),
                    html.P(f"{z['available']}/{z['total']} Available ({z['percentage']:.0f}%)",
                           style={'color': TEXT_SECONDARY, 'margin': 0}),
                    html.Div(
                        style={'width': '100%', 'height': '8px', 'backgroundColor': '#334155', 'borderRadius': '4px',
                               'marginTop': '8px', 'overflow': 'hidden'},
                        children=[html.Div(style={'width': f"{z['percentage']}%", 'height': '100%',
                                                  'backgroundColor': SUCCESS_COLOR if z[
                                                                                          'percentage'] > 50 else WARNING_COLOR if
                                                  z['percentage'] > 20 else DANGER_COLOR})])
                ], style={'backgroundColor': CARD_BG, 'padding': '20px', 'borderRadius': '8px'})
                for z in zone_data
            ], style={'display': 'grid', 'gridTemplateColumns': '1fr 1fr 1fr 1fr', 'gap': '15px',
                      'marginBottom': '20px'})
        ], style=CHART_CARD),

        html.Div([
            html.H3("All Parking Slots", style={'color': TEXT_PRIMARY, 'marginBottom': '15px'}),
            dash_table.DataTable(
                data=df.to_dict('records'),
                columns=[{"name": i, "id": i} for i in
                         ['Slot ID', 'Zone', 'Status', 'Vehicle', 'License', 'Duration', 'Total']],
                style_table={'overflowX': 'auto'},
                style_cell={'backgroundColor': CARD_BG, 'color': TEXT_PRIMARY, 'textAlign': 'left', 'padding': '12px',
                            'border': '1px solid #334155'},
                style_header={'backgroundColor': '#334155', 'fontWeight': 'bold', 'border': '1px solid #475569'},
                style_data_conditional=[
                    {'if': {'filter_query': '{Status} = "OCCUPIED"'}, 'backgroundColor': '#1e293b'},
                    {'if': {'filter_query': '{Status} = "AVAILABLE"'}, 'backgroundColor': '#0f172a'},
                ],
                page_size=15, sort_action='native', filter_action='native'
            )
        ], style=CHART_CARD)
    ])


def render_bookings_content():
    bookings = get_bookings()
    return html.Div([
        html.H2("📝 Booking Management", style={'color': TEXT_PRIMARY, 'marginBottom': '20px'}),
        html.Div([
            html.Div([html.Div([
                html.H3(f"{len([b for b in bookings if b['status'] == 'active'])}",
                        style={'color': SUCCESS_COLOR, 'margin': 0, 'fontSize': '32px'}),
                html.P("Active Bookings", style={'color': TEXT_SECONDARY, 'margin': 0})
            ], style=METRIC_CARD)], style={'flex': 1, 'marginRight': '10px'}),

            html.Div([html.Div([
                html.H3(f"{len([b for b in bookings if b['status'] == 'completed'])}",
                        style={'color': TEXT_SECONDARY, 'margin': 0, 'fontSize': '32px'}),
                html.P("Completed", style={'color': TEXT_SECONDARY, 'margin': 0})
            ], style=METRIC_CARD)], style={'flex': 1, 'marginRight': '10px'}),

            html.Div([html.Div([
                html.H3(f"Rs {sum([b['cost'] for b in bookings]):,.0f}",
                        style={'color': ACCENT_COLOR, 'margin': 0, 'fontSize': '28px'}),
                html.P("Total Value", style={'color': TEXT_SECONDARY, 'margin': 0})
            ], style=METRIC_CARD)], style={'flex': 1})
        ], style={'display': 'flex', 'gap': '15px', 'marginBottom': '20px'}),

        html.Div([
            html.Div([
                html.Div([
                    html.Div([
                        html.H4(f"{b['name']}", style={'color': TEXT_PRIMARY, 'margin': 0}),
                        html.P(f"📞 {b['phone']} | 🚗 {b['vehicle']} | {b['license']}",
                               style={'color': TEXT_SECONDARY, 'margin': '5px 0', 'fontSize': '14px'}),
                        html.P(
                            f"Slot: {b['slot']} ({b['zone']}) | Duration: {b['duration']}h | Cost: Rs {b['cost']:.2f}",
                            style={'color': TEXT_SECONDARY, 'margin': '5px 0', 'fontSize': '14px'}),
                        html.P(f"Booked: {b['timestamp']}",
                               style={'color': TEXT_SECONDARY, 'margin': '5px 0', 'fontSize': '12px'})
                    ], style={'flex': 1}),
                    html.Div([
                        html.Span("✅ ACTIVE" if b['status'] == 'active' else "✓ COMPLETED",
                                  style={'padding': '6px 12px', 'borderRadius': '6px', 'fontSize': '12px',
                                         'fontWeight': 'bold',
                                         'backgroundColor': SUCCESS_COLOR if b[
                                                                                 'status'] == 'active' else TEXT_SECONDARY,
                                         'color': 'white'})
                    ])
                ], style={'display': 'flex', 'justifyContent': 'space-between', 'alignItems': 'center'})
            ], style={'backgroundColor': CARD_BG, 'padding': '20px', 'borderRadius': '8px', 'marginBottom': '10px'})
            for b in bookings[:20]
        ])
    ])


def render_activity_content():
    logs = get_activity_log()
    return html.Div([
        html.H2("🔍 Activity Log", style={'color': TEXT_PRIMARY, 'marginBottom': '20px'}),
        html.Div([
            html.Div([
                html.Div([
                    html.Span(log['timestamp'].strftime("%Y-%m-%d %H:%M:%S") if hasattr(log['timestamp'],
                                                                                        'strftime') else str(
                        log['timestamp']),
                              style={'color': TEXT_SECONDARY, 'fontSize': '12px', 'width': '150px'}),
                    html.Span(f"👤 {log['user_name']}",
                              style={'color': ACCENT_COLOR, 'fontSize': '14px', 'width': '120px',
                                     'marginLeft': '15px'}),
                    html.Span(f"📋 {log['action']}", style={'color': TEXT_PRIMARY, 'fontSize': '14px', 'width': '150px',
                                                           'marginLeft': '15px'}),
                    html.Span(log['details'],
                              style={'color': TEXT_SECONDARY, 'fontSize': '14px', 'flex': 1, 'marginLeft': '15px'})
                ], style={'display': 'flex', 'alignItems': 'center', 'padding': '15px', 'backgroundColor': CARD_BG,
                          'borderRadius': '8px', 'marginBottom': '8px'})
            ]) for log in logs
        ])
    ])


# App Layout
app.layout = html.Div([
    dcc.Location(id='url', refresh=False),
    dcc.Store(id='session-store', storage_type='session'),
    dcc.Store(id='view-store', data='dashboard'),
    html.Div(id='page-content'),
    dcc.Interval(id='interval-component', interval=10 * 1000, n_intervals=0)
], style={'backgroundColor': MAIN_BG, 'minHeight': '100vh'})


# Callbacks
@app.callback(
    Output('page-content', 'children'),
    [Input('url', 'pathname'), Input('public-booking-button', 'n_clicks'), Input('back-to-login', 'n_clicks')],
    State('session-store', 'data'), prevent_initial_call=False
)
def display_page(pathname, public_clicks, back_clicks, session_data):
    ctx = dash.callback_context
    if ctx.triggered:
        button_id = ctx.triggered[0]['prop_id'].split('.')[0]
        if button_id == 'public-booking-button':
            return public_booking_layout()
        elif button_id == 'back-to-login':
            return login_layout()

    if session_data and session_data.get('authenticated'):
        return admin_dashboard_layout()
    return login_layout()


@app.callback(
    [Output('session-store', 'data'), Output('url', 'pathname'), Output('login-alert', 'children')],
    Input('login-button', 'n_clicks'),
    [State('username-input', 'value'), State('password-input', 'value')],
    prevent_initial_call=True
)
def login(n_clicks, username, password):
    if n_clicks > 0:
        if username in ADMIN_CREDENTIALS and ADMIN_CREDENTIALS[username] == password:
            log_activity("User Login", f"User {username} logged in", username)
            return {'authenticated': True, 'username': username}, '/dashboard', ''
        else:
            return dash.no_update, dash.no_update, '❌ Invalid credentials'
    return dash.no_update, dash.no_update, ''


@app.callback(
    [Output('session-store', 'data', allow_duplicate=True), Output('url', 'pathname', allow_duplicate=True)],
    Input('logout-button', 'n_clicks'), State('session-store', 'data'), prevent_initial_call=True
)
def logout(n_clicks, session_data):
    if n_clicks > 0:
        username = session_data.get('username', 'Unknown') if session_data else 'Unknown'
        log_activity("User Logout", f"User {username} logged out", username)
        return None, '/'
    return dash.no_update, dash.no_update


@app.callback(
    Output('view-store', 'data'),
    [Input('nav-dashboard', 'n_clicks'), Input('nav-bookings', 'n_clicks'), Input('nav-activity', 'n_clicks')],
    prevent_initial_call=False
)
def navigation(dash_clicks, book_clicks, act_clicks):
    ctx = dash.callback_context
    if ctx.triggered:
        button_id = ctx.triggered[0]['prop_id'].split('.')[0]
        if button_id == 'nav-dashboard':
            return 'dashboard'
        elif button_id == 'nav-bookings':
            return 'bookings'
        elif button_id == 'nav-activity':
            return 'activity'
    return 'dashboard'


@app.callback(
    Output('admin-content', 'children'),
    [Input('interval-component', 'n_intervals'), Input('view-store', 'data')],
    State('session-store', 'data'), prevent_initial_call=False
)
def update_admin_content(n, view, session_data):
    if not session_data or not session_data.get('authenticated'):
        return html.Div()

    df = get_parking_data()
    if df.empty:
        return html.Div([
            html.H2("⚠️ Database Connection Error",
                    style={'color': WARNING_COLOR, 'textAlign': 'center', 'marginTop': '50px'}),
            html.P("Cannot connect to PostgreSQL database.", style={'color': TEXT_SECONDARY, 'textAlign': 'center'}),
            html.P(f"DATABASE_URL: {DATABASE_URL[:50]}...",
                   style={'color': TEXT_SECONDARY, 'fontSize': '12px', 'textAlign': 'center'}),
            html.P("Please check your database connection.", style={'color': DANGER_COLOR, 'textAlign': 'center'})
        ], style={'backgroundColor': CARD_BG, 'padding': '40px', 'borderRadius': '12px', 'margin': '20px'})

    stats = get_statistics(df)

    if view == 'bookings':
        return render_bookings_content()
    elif view == 'activity':
        return render_activity_content()
    else:
        return render_dashboard_content(df, stats)


@app.callback(
    Output('public-stats', 'children'),
    Input('interval-component', 'n_intervals'), prevent_initial_call=False
)
def update_public_stats(n):
    df = get_parking_data()
    if df.empty:
        return html.Div("Service temporarily unavailable", style={'color': DANGER_COLOR})

    stats = get_statistics(df)
    return html.Div([
        html.Div([
            html.H3(f"{stats['available']}", style={'color': SUCCESS_COLOR, 'margin': 0}),
            html.P("Available", style={'color': TEXT_SECONDARY, 'margin': 0})
        ], style={'flex': 1, 'textAlign': 'center', 'padding': '15px', 'backgroundColor': CARD_BG,
                  'borderRadius': '8px'}),

        html.Div([
            html.H3(f"Rs {get_dynamic_rate()}", style={'color': ACCENT_COLOR, 'margin': 0}),
            html.P("Per Hour", style={'color': TEXT_SECONDARY, 'margin': 0})
        ], style={'flex': 1, 'textAlign': 'center', 'padding': '15px', 'backgroundColor': CARD_BG,
                  'borderRadius': '8px', 'marginLeft': '10px'})
    ], style={'display': 'flex'})


@app.callback(
    Output('booking-cost-display', 'children'),
    Input('booking-duration', 'value'), prevent_initial_call=False
)
def update_cost(duration):
    if duration and duration > 0:
        cost = duration * get_dynamic_rate()
        return f"Estimated Cost: Rs {cost:.2f}"
    return ""


@app.callback(
    Output('booking-result', 'children'),
    Input('submit-booking', 'n_clicks'),
    [State('booking-name', 'value'), State('booking-phone', 'value'), State('booking-vehicle', 'value'),
     State('booking-license', 'value'), State('booking-duration', 'value')],
    prevent_initial_call=True
)
def submit_booking(n_clicks, name, phone, vehicle, license_plate, duration):
    if n_clicks > 0:
        if not all([name, phone, vehicle, license_plate, duration]):
            return html.Div("❌ Please fill all fields",
                            style={'color': DANGER_COLOR, 'padding': '10px', 'backgroundColor': CARD_BG,
                                   'borderRadius': '6px'})

        df = get_parking_data()
        available_df = df[df['Status'] == 'AVAILABLE']

        if len(available_df) == 0:
            return html.Div("❌ No slots available",
                            style={'color': DANGER_COLOR, 'padding': '10px', 'backgroundColor': CARD_BG,
                                   'borderRadius': '6px'})

        slot = available_df.iloc[0]['Slot ID']
        zone = available_df.iloc[0]['Zone']
        cost = duration * get_dynamic_rate()
        booking_id = f"BK{datetime.datetime.now().strftime('%m%d%H%M%S')}"

        booking = {'id': booking_id, 'name': name, 'phone': phone, 'vehicle': vehicle,
                   'license': license_plate, 'slot': slot, 'zone': zone, 'duration': duration, 'cost': cost}

        result = create_booking_in_db(booking)
        if result:
            log_activity("Public Booking", f"Booking {booking_id} for {name}", name)
            return html.Div([
                html.H3("✅ Booking Confirmed!", style={'color': SUCCESS_COLOR}),
                html.P(f"Booking ID: {booking_id}", style={'color': TEXT_PRIMARY}),
                html.P(f"Slot: {slot} ({zone})", style={'color': TEXT_PRIMARY}),
                html.P(f"Cost: Rs {cost:.2f}", style={'color': TEXT_PRIMARY}),
                html.P("Please arrive within 30 minutes", style={'color': TEXT_SECONDARY, 'fontSize': '12px'})
            ], style={'padding': '20px', 'backgroundColor': CARD_BG, 'borderRadius': '8px', 'marginTop': '15px'})
        else:
            return html.Div("❌ Booking failed. Please try again.", style={'color': DANGER_COLOR})

    return ""


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)