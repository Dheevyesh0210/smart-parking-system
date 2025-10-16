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

# ------------------------------
# Configuration
# ------------------------------
TOTAL_SLOTS = 100
HOURLY_RATE = 50  # Mauritian Rupees per hour
FREE_HOURS = 2
PENALTY_RATE = 100  # Mauritian Rupees per hour after free period
PEAK_HOUR_MULTIPLIER = 1.5  # Dynamic pricing during peak hours
PEAK_HOURS = [(7, 10), (17, 20)]  # 7-10 AM and 5-8 PM

# Admin credentials (in production, use proper authentication with hashed passwords)
ADMIN_CREDENTIALS = {
    "admin": "admin123",
    "operator": "operator123"
}

USER_ROLES = {
    "admin": "admin",
    "operator": "operator"
}

# Historical data storage
occupancy_history = deque(maxlen=50)
revenue_history = deque(maxlen=50)
timestamp_history = deque(maxlen=50)

# Parking data structure
parking_data = {
    f"P{str(i + 1).zfill(3)}": {
        "status": "available",
        "entry_time": None,
        "zone": f"Zone-{chr(65 + i // 25)}",
        "vehicle_type": None,
        "license_plate": None,
        "customer_id": None,
        "is_reserved": False,
        "maintenance": False
    } for i in range(TOTAL_SLOTS)
}

# Initialize with some occupied slots
for i in range(35):
    slot_id = f"P{str(i + 1).zfill(3)}"
    parking_data[slot_id]["status"] = "occupied"
    parking_data[slot_id]["entry_time"] = (datetime.datetime.now() - datetime.timedelta(
        hours=random.uniform(0, 8))).strftime("%Y-%m-%d %H:%M:%S")
    parking_data[slot_id]["vehicle_type"] = random.choice(["Car", "Bike", "SUV"])
    parking_data[slot_id]["license_plate"] = f"MU-{random.randint(1000, 9999)}"
    parking_data[slot_id]["customer_id"] = f"C{random.randint(1000, 9999)}"

# Booking system
bookings = []
booking_counter = 1

# Alert system
alerts = []

# Activity log
activity_log = deque(maxlen=100)


def log_activity(action, details, user="System"):
    activity_log.append({
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "user": user,
        "action": action,
        "details": details
    })


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
    for slot_id, details in parking_data.items():
        if details["maintenance"]:
            continue

        if random.random() < 0.08:
            if details["status"] == "available" and not details["is_reserved"]:
                details["status"] = "occupied"
                details["entry_time"] = (datetime.datetime.now() - datetime.timedelta(
                    hours=random.uniform(0, 6))).strftime("%Y-%m-%d %H:%M:%S")
                details["vehicle_type"] = random.choice(["Car", "Bike", "SUV"])
                details["license_plate"] = f"MU-{random.randint(1000, 9999)}"
                details["customer_id"] = f"C{random.randint(1000, 9999)}"
            else:
                details["status"] = "available"
                details["entry_time"] = None
                details["vehicle_type"] = None
                details["license_plate"] = None
                details["customer_id"] = None


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


def get_parking_data():
    rows = []
    for slot_id, details in parking_data.items():
        status = details.get("status", "available")
        entry_time = details.get("entry_time", None)
        zone = details.get("zone", "Zone-A")
        vehicle_type = details.get("vehicle_type", "-")
        license_plate = details.get("license_plate", "-")
        customer_id = details.get("customer_id", "-")
        is_reserved = details.get("is_reserved", False)
        maintenance = details.get("maintenance", False)

        fine = 0
        revenue = 0
        duration_str = "-"
        duration_hours = 0

        if maintenance:
            status = "maintenance"

        if status == "occupied" and entry_time:
            entry_dt = datetime.datetime.strptime(entry_time, "%Y-%m-%d %H:%M:%S")
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
            "Slot ID": slot_id,
            "Zone": zone,
            "Status": status.upper(),
            "Vehicle": vehicle_type if status == "occupied" else "-",
            "License": license_plate if status == "occupied" else "-",
            "Customer ID": customer_id if status == "occupied" else "-",
            "Entry Time": entry_time if entry_time else "-",
            "Duration": duration_str,
            "Rate": f"Rs {get_dynamic_rate()}/hr" if status == "occupied" else "-",
            "Parking Fee": f"Rs {revenue}",
            "Overstay Fine": f"Rs {fine}",
            "Total": f"Rs {revenue + fine}",
            "_revenue": revenue,
            "_fine": fine,
            "_duration_hours": duration_hours,
            "_is_reserved": is_reserved,
            "_maintenance": maintenance
        })
    return pd.DataFrame(rows)


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

        # Create booking
        global booking_counter
        booking_id = f"BK{str(booking_counter).zfill(4)}"
        booking_counter += 1

        booking = {
            'id': booking_id,
            'name': data['name'],
            'phone': data['phone'],
            'vehicle': data['vehicle'],
            'license': data['license'],
            'slot': slot,
            'zone': selected_zone,
            'duration': duration,
            'cost': round(cost, 2),
            'timestamp': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'status': 'active'
        }

        # Add to bookings
        bookings.append(booking)

        # Mark slot as reserved
        parking_data[slot]['is_reserved'] = True
        parking_data[slot]['vehicle_type'] = data['vehicle']
        parking_data[slot]['license_plate'] = data['license']

        # Log activity
        log_activity("Booking Created", f"Booking {booking_id} for slot {slot}", data['name'])

        return jsonify({
            'success': True,
            'booking': booking,
            'message': 'Booking created successfully'
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@server.route('/api/bookings', methods=['GET'])
def get_bookings_api():
    """Get all bookings"""
    try:
        # Filter active bookings or all based on query parameter
        status_filter = request.args.get('status', None)

        if status_filter:
            filtered_bookings = [b for b in bookings if b.get('status', 'active') == status_filter]
        else:
            filtered_bookings = bookings

        return jsonify({
            'success': True,
            'bookings': filtered_bookings,
            'count': len(filtered_bookings)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@server.route('/api/booking/<booking_id>', methods=['GET'])
def get_booking_api(booking_id):
    """Get a specific booking"""
    try:
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
        # Find booking
        booking = next((b for b in bookings if b['id'] == booking_id), None)

        if not booking:
            return jsonify({'success': False, 'error': 'Booking not found'}), 404

        if booking.get('status', 'active') != 'active':
            return jsonify({'success': False, 'error': 'Booking is not active'}), 400

        # Update booking status
        booking['status'] = 'completed'
        booking['checkout_time'] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Free up the slot
        if booking['slot'] in parking_data:
            parking_data[booking['slot']]['is_reserved'] = False
            parking_data[booking['slot']]['status'] = 'available'
            parking_data[booking['slot']]['vehicle_type'] = None
            parking_data[booking['slot']]['license_plate'] = None

        log_activity("Booking Completed", f"Booking {booking_id} checked out", booking['name'])

        return jsonify({
            'success': True,
            'message': 'Checked out successfully',
            'booking': booking
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@server.route('/api/stats', methods=['GET'])
def get_stats_api():
    """Get detailed statistics"""
    try:
        df = get_parking_data()
        stats = get_statistics(df)

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
                'active_bookings': len([b for b in bookings if b.get('status', 'active') == 'active']),
                'total_bookings': len(bookings),
                'current_rate': get_dynamic_rate()
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ------------------------------
# Styles
# ------------------------------
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

# ------------------------------
# Layout
# ------------------------------
app.layout = html.Div([
    dcc.Location(id='url', refresh=False),
    dcc.Store(id='session-store', storage_type='session'),
    html.Div(id='page-content'),
    dcc.Interval(id='interval-component', interval=30 * 1000, n_intervals=0)
], style={'backgroundColor': MAIN_BG, 'minHeight': '100vh'})


# ------------------------------
# Login Page
# ------------------------------
def login_page():
    return html.Div([
        html.Div([
            html.Div([
                html.H1("🅿️ Smart Parking System",
                        style={'color': TEXT_PRIMARY, 'marginBottom': '10px', 'textAlign': 'center',
                               'fontSize': '32px'}),
                html.P("Advanced Parking Management",
                       style={'color': TEXT_SECONDARY, 'textAlign': 'center', 'marginBottom': '30px',
                              'fontSize': '14px'}),

                html.Div([
                    html.Label("Username", style={'color': TEXT_PRIMARY, 'display': 'block', 'marginBottom': '8px',
                                                  'fontWeight': '600'}),
                    dcc.Input(
                        id='login-username',
                        type='text',
                        placeholder='Enter username',
                        style={
                            'width': '100%',
                            'padding': '12px',
                            'borderRadius': '6px',
                            'border': '1px solid #334155',
                            'backgroundColor': MAIN_BG,
                            'color': TEXT_PRIMARY,
                            'fontSize': '14px',
                            'marginBottom': '20px'
                        }
                    ),

                    html.Label("Password", style={'color': TEXT_PRIMARY, 'display': 'block', 'marginBottom': '8px',
                                                  'fontWeight': '600'}),
                    dcc.Input(
                        id='login-password',
                        type='password',
                        placeholder='Enter password',
                        style={
                            'width': '100%',
                            'padding': '12px',
                            'borderRadius': '6px',
                            'border': '1px solid #334155',
                            'backgroundColor': MAIN_BG,
                            'color': TEXT_PRIMARY,
                            'fontSize': '14px',
                            'marginBottom': '20px'
                        }
                    ),

                    html.Button(
                        '🔐 Admin Login',
                        id='login-button',
                        n_clicks=0,
                        style={
                            **BUTTON_STYLE,
                            'width': '100%',
                            'marginBottom': '15px',
                            'padding': '14px'
                        }
                    ),

                    html.Div(id='login-error',
                             style={'color': DANGER_COLOR, 'textAlign': 'center', 'marginBottom': '20px',
                                    'minHeight': '20px'}),

                    html.Hr(style={'border': '1px solid #334155', 'margin': '25px 0'}),

                    html.P("Public Booking Access",
                           style={'color': TEXT_SECONDARY, 'textAlign': 'center', 'marginBottom': '15px',
                                  'fontWeight': '600', 'fontSize': '13px'}),

                    html.Button(
                        '📅 Book a Parking Slot',
                        id='public-booking-button',
                        n_clicks=0,
                        style={
                            **BUTTON_STYLE,
                            'backgroundColor': SUCCESS_COLOR,
                            'width': '100%',
                            'padding': '14px'
                        }
                    ),
                ], style={'padding': '0 20px'}),

                html.Div([
                    html.P("Demo Credentials:",
                           style={'color': TEXT_SECONDARY, 'fontSize': '12px', 'marginBottom': '8px',
                                  'fontWeight': '600'}),
                    html.P("admin / admin123", style={'color': TEXT_SECONDARY, 'fontSize': '11px', 'margin': '4px 0'}),
                    html.P("operator / operator123",
                           style={'color': TEXT_SECONDARY, 'fontSize': '11px', 'margin': '4px 0'}),
                ], style={'marginTop': '25px', 'padding': '15px', 'backgroundColor': MAIN_BG, 'borderRadius': '6px',
                          'textAlign': 'center'})

            ], style={
                'backgroundColor': CARD_BG,
                'padding': '40px',
                'borderRadius': '12px',
                'boxShadow': '0 8px 16px rgba(0,0,0,0.4)',
                'border': '1px solid #334155',
                'width': '100%',
                'maxWidth': '450px'
            })
        ], style={
            'display': 'flex',
            'justifyContent': 'center',
            'alignItems': 'center',
            'minHeight': '100vh',
            'padding': '20px'
        })
    ], style={'backgroundColor': MAIN_BG, 'minHeight': '100vh', 'fontFamily': 'Arial, sans-serif'})


# ------------------------------
# Public Booking Page
# ------------------------------
def render_public_booking():
    df = get_parking_data()
    stats = get_statistics(df)

    return html.Div([
        # Header
        html.Div([
            html.Div([
                html.H1("🅿️ Parking Slot Booking",
                        style={'margin': '0', 'fontSize': '28px', 'fontWeight': '700',
                               'color': TEXT_PRIMARY}),
                html.P("Reserve your parking spot in advance",
                       style={'margin': '8px 0 0 0', 'fontSize': '14px',
                              'color': TEXT_SECONDARY})
            ], style={'flex': '1'}),

            html.Button(
                '🏠 Back to Home',
                id='back-to-home',
                n_clicks=0,
                style=BUTTON_STYLE
            )
        ], style={
            'backgroundColor': CARD_BG,
            'padding': '24px 32px',
            'borderRadius': '12px',
            'marginBottom': '24px',
            'border': '1px solid #334155',
            'boxShadow': '0 4px 6px -1px rgba(0, 0, 0, 0.3)',
            'display': 'flex',
            'justifyContent': 'space-between',
            'alignItems': 'center'
        }),

        # Availability Info
        html.Div([
            html.Div([
                html.Div([
                    html.Span("✅", style={'fontSize': '32px'}),
                    html.Div([
                        html.H2(f"{stats['available']}",
                                style={'color': SUCCESS_COLOR, 'margin': '0', 'fontSize': '36px', 'fontWeight': '700'}),
                        html.P("Available Slots",
                               style={'color': TEXT_SECONDARY, 'margin': '5px 0 0 0', 'fontSize': '13px'})
                    ])
                ], style={'display': 'flex', 'alignItems': 'center', 'gap': '15px'})
            ], style={**CHART_CARD, 'width': '32%', 'display': 'inline-block', 'marginRight': '1%'}),

            html.Div([
                html.Div([
                    html.Span("💰", style={'fontSize': '32px'}),
                    html.Div([
                        html.H2(f"Rs {get_dynamic_rate()}/hr",
                                style={'color': WARNING_COLOR, 'margin': '0', 'fontSize': '32px', 'fontWeight': '700'}),
                        html.P("Current Rate",
                               style={'color': TEXT_SECONDARY, 'margin': '5px 0 0 0', 'fontSize': '13px'})
                    ])
                ], style={'display': 'flex', 'alignItems': 'center', 'gap': '15px'})
            ], style={**CHART_CARD, 'width': '32%', 'display': 'inline-block', 'marginRight': '1%'}),

            html.Div([
                html.Div([
                    html.Span("📈", style={'fontSize': '32px'}),
                    html.Div([
                        html.H2(f"{stats['occupancy_rate']:.0f}%",
                                style={'color': ACCENT_COLOR, 'margin': '0', 'fontSize': '36px', 'fontWeight': '700'}),
                        html.P("Occupancy", style={'color': TEXT_SECONDARY, 'margin': '5px 0 0 0', 'fontSize': '13px'})
                    ])
                ], style={'display': 'flex', 'alignItems': 'center', 'gap': '15px'})
            ], style={**CHART_CARD, 'width': '32%', 'display': 'inline-block'}),
        ], style={'marginBottom': '24px'}),

        # Booking Form
        html.Div([
            html.H3("📝 Make a Reservation",
                    style={'color': TEXT_PRIMARY, 'marginBottom': '20px', 'fontSize': '20px', 'fontWeight': '600'}),

            html.Div([
                html.Div([
                    html.Label("Full Name *", style={'color': TEXT_SECONDARY, 'display': 'block', 'marginBottom': '8px',
                                                     'fontWeight': '600', 'fontSize': '13px'}),
                    dcc.Input(
                        id='booking-name',
                        type='text',
                        placeholder='Enter your full name',
                        style={
                            'width': '100%',
                            'padding': '12px',
                            'borderRadius': '6px',
                            'border': '1px solid #334155',
                            'backgroundColor': MAIN_BG,
                            'color': TEXT_PRIMARY,
                            'fontSize': '14px'
                        }
                    )
                ], style={'width': '48%', 'display': 'inline-block', 'marginRight': '2%'}),

                html.Div([
                    html.Label("Phone Number *",
                               style={'color': TEXT_SECONDARY, 'display': 'block', 'marginBottom': '8px',
                                      'fontWeight': '600', 'fontSize': '13px'}),
                    dcc.Input(
                        id='booking-phone',
                        type='text',
                        placeholder='Enter phone number',
                        style={
                            'width': '100%',
                            'padding': '12px',
                            'borderRadius': '6px',
                            'border': '1px solid #334155',
                            'backgroundColor': MAIN_BG,
                            'color': TEXT_PRIMARY,
                            'fontSize': '14px'
                        }
                    )
                ], style={'width': '48%', 'display': 'inline-block'}),
            ], style={'marginBottom': '20px'}),

            html.Div([
                html.Div([
                    html.Label("Vehicle Type *",
                               style={'color': TEXT_SECONDARY, 'display': 'block', 'marginBottom': '8px',
                                      'fontWeight': '600', 'fontSize': '13px'}),
                    dcc.Dropdown(
                        id='booking-vehicle',
                        options=[
                            {'label': '🚗 Car', 'value': 'Car'},
                            {'label': '🏍️ Bike', 'value': 'Bike'},
                            {'label': '🚙 SUV', 'value': 'SUV'}
                        ],
                        placeholder='Select vehicle type',
                        style={'backgroundColor': MAIN_BG}
                    )
                ], style={'width': '32%', 'display': 'inline-block', 'marginRight': '2%'}),

                html.Div([
                    html.Label("License Plate *",
                               style={'color': TEXT_SECONDARY, 'display': 'block', 'marginBottom': '8px',
                                      'fontWeight': '600', 'fontSize': '13px'}),
                    dcc.Input(
                        id='booking-license',
                        type='text',
                        placeholder='e.g., MU-1234',
                        style={
                            'width': '100%',
                            'padding': '12px',
                            'borderRadius': '6px',
                            'border': '1px solid #334155',
                            'backgroundColor': MAIN_BG,
                            'color': TEXT_PRIMARY,
                            'fontSize': '14px'
                        }
                    )
                ], style={'width': '32%', 'display': 'inline-block', 'marginRight': '2%'}),

                html.Div([
                    html.Label("Duration (hours) *",
                               style={'color': TEXT_SECONDARY, 'display': 'block', 'marginBottom': '8px',
                                      'fontWeight': '600', 'fontSize': '13px'}),
                    dcc.Input(
                        id='booking-duration',
                        type='number',
                        value=2,
                        min=1,
                        max=24,
                        style={
                            'width': '100%',
                            'padding': '12px',
                            'borderRadius': '6px',
                            'border': '1px solid #334155',
                            'backgroundColor': MAIN_BG,
                            'color': TEXT_PRIMARY,
                            'fontSize': '14px'
                        }
                    )
                ], style={'width': '32%', 'display': 'inline-block'}),
            ], style={'marginBottom': '20px'}),

            html.Div([
                html.Label("Preferred Zone", style={'color': TEXT_SECONDARY, 'display': 'block', 'marginBottom': '8px',
                                                    'fontWeight': '600', 'fontSize': '13px'}),
                dcc.Dropdown(
                    id='booking-zone',
                    options=[
                        {'label': 'Zone-A (Nearest to entrance)', 'value': 'Zone-A'},
                        {'label': 'Zone-B', 'value': 'Zone-B'},
                        {'label': 'Zone-C', 'value': 'Zone-C'},
                        {'label': 'Zone-D', 'value': 'Zone-D'}
                    ],
                    placeholder='Select preferred zone (optional)',
                    style={'backgroundColor': MAIN_BG}
                )
            ], style={'marginBottom': '25px'}),

            html.Div([
                html.Button(
                    '🎫 Confirm Booking',
                    id='confirm-booking',
                    n_clicks=0,
                    style={**BUTTON_STYLE, 'backgroundColor': SUCCESS_COLOR, 'fontSize': '16px', 'padding': '14px 32px'}
                ),
                html.Div(id='booking-confirmation', style={'display': 'inline-block', 'marginLeft': '20px'})
            ])
        ], style={
            'backgroundColor': CARD_BG,
            'padding': '30px',
            'borderRadius': '12px',
            'border': '1px solid #334155',
            'boxShadow': '0 4px 6px -1px rgba(0, 0, 0, 0.3)',
            'marginBottom': '24px'
        }),

        # Info Section
        html.Div([
            html.H3("ℹ️ Booking Information",
                    style={'color': TEXT_PRIMARY, 'marginBottom': '15px', 'fontSize': '18px', 'fontWeight': '600'}),
            html.Ul([
                html.Li(f"Standard Rate: Rs {HOURLY_RATE}/hour", style={'marginBottom': '8px', 'color': TEXT_PRIMARY}),
                html.Li(f"Peak Hour Rate: Rs {int(HOURLY_RATE * PEAK_HOUR_MULTIPLIER)}/hour (7-10 AM & 5-8 PM)",
                        style={'marginBottom': '8px', 'color': TEXT_PRIMARY}),
                html.Li(f"First {FREE_HOURS} hours: No penalty", style={'marginBottom': '8px', 'color': TEXT_PRIMARY}),
                html.Li(f"Overstay Penalty: Rs {PENALTY_RATE}/hour after free period",
                        style={'marginBottom': '8px', 'color': TEXT_PRIMARY}),
                html.Li("You will receive a confirmation with your slot number after booking",
                        style={'color': TEXT_PRIMARY}),
            ], style={'color': TEXT_SECONDARY})
        ], style={
            'backgroundColor': CARD_BG,
            'padding': '24px',
            'borderRadius': '12px',
            'border': '1px solid #334155',
            'boxShadow': '0 4px 6px -1px rgba(0, 0, 0, 0.3)'
        })

    ], style={'padding': '24px', 'maxWidth': '1200px', 'margin': '0 auto', 'fontFamily': 'Arial, sans-serif'})


# ------------------------------
# Callbacks
# ------------------------------

# Page routing
@app.callback(
    Output('page-content', 'children'),
    [Input('url', 'pathname')],
    [State('session-store', 'data')]
)
def display_page(pathname, session_data):
    if not session_data:
        session_data = {'authenticated': False}

    if not pathname or pathname == '/' or pathname == '/login':
        return login_page()
    elif pathname == '/booking':
        return render_public_booking()
    elif pathname == '/dashboard':
        if session_data.get('authenticated'):
            return render_admin_app(session_data)
        else:
            return login_page()
    else:
        return login_page()


# Login handler
@app.callback(
    [Output('url', 'pathname'),
     Output('session-store', 'data'),
     Output('login-error', 'children')],
    [Input('login-button', 'n_clicks'),
     Input('public-booking-button', 'n_clicks')],
    [State('login-username', 'value'),
     State('login-password', 'value'),
     State('session-store', 'data')],
    prevent_initial_call=True
)
def handle_login(login_clicks, booking_clicks, username, password, session_data):
    if not session_data:
        session_data = {'authenticated': False, 'user': None, 'role': None}

    ctx_triggered = dash.callback_context.triggered[0]['prop_id'].split('.')[
        0] if dash.callback_context.triggered else None

    if not ctx_triggered:
        return dash.no_update, dash.no_update, dash.no_update

    if ctx_triggered == 'login-button':
        if username and password:
            if username in ADMIN_CREDENTIALS and ADMIN_CREDENTIALS[username] == password:
                role = USER_ROLES.get(username, 'viewer')
                log_activity("User Login", f"User {username} logged in", username)
                return '/dashboard', {'authenticated': True, 'user': username, 'role': role}, ''
            else:
                return dash.no_update, session_data, '❌ Invalid credentials. Please try again.'
        return dash.no_update, session_data, '⚠️ Please enter both username and password.'

    elif ctx_triggered == 'public-booking-button':
        return '/booking', session_data, ''

    return dash.no_update, dash.no_update, dash.no_update


# Back to home handler (separate callback for booking page)
@app.callback(
    Output('url', 'pathname', allow_duplicate=True),
    Input('back-to-home', 'n_clicks'),
    prevent_initial_call=True
)
def go_back_home(n_clicks):
    if n_clicks and n_clicks > 0:
        return '/login'
    return dash.no_update


# Booking confirmation
@app.callback(
    Output('booking-confirmation', 'children'),
    Input('confirm-booking', 'n_clicks'),
    [State('booking-name', 'value'),
     State('booking-phone', 'value'),
     State('booking-vehicle', 'value'),
     State('booking-license', 'value'),
     State('booking-duration', 'value'),
     State('booking-zone', 'value')],
    prevent_initial_call=True
)
def confirm_booking(n_clicks, name, phone, vehicle, license_plate, duration, zone):
    if n_clicks and n_clicks > 0:
        if not all([name, phone, vehicle, license_plate, duration]):
            return html.Div("⚠️ Please fill in all required fields",
                            style={'color': WARNING_COLOR, 'fontWeight': '600'})

        global booking_counter
        df = get_parking_data()
        available_df = df[df['Status'] == 'AVAILABLE']

        if zone:
            zone_available = available_df[available_df['Zone'] == zone]
            if len(zone_available) > 0:
                slot = zone_available.iloc[0]['Slot ID']
            elif len(available_df) > 0:
                slot = available_df.iloc[0]['Slot ID']
            else:
                return html.Div("❌ No slots available",
                                style={'color': DANGER_COLOR, 'fontWeight': '600'})
        else:
            if len(available_df) > 0:
                slot = available_df.iloc[0]['Slot ID']
            else:
                return html.Div("❌ No slots available",
                                style={'color': DANGER_COLOR, 'fontWeight': '600'})

        estimated_cost = duration * get_dynamic_rate()
        booking_id = f"BK{str(booking_counter).zfill(4)}"
        booking_counter += 1

        bookings.append({
            'id': booking_id,
            'name': name,
            'phone': phone,
            'vehicle': vehicle,
            'license': license_plate,
            'slot': slot,
            'duration': duration,
            'cost': estimated_cost,
            'timestamp': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'status': 'active'
        })

        parking_data[slot]['is_reserved'] = True
        log_activity("Booking Created", f"Booking {booking_id} for slot {slot}", name)

        return html.Div([
            html.H4("✅ Booking Confirmed!", style={'color': SUCCESS_COLOR, 'margin': '0 0 10px 0'}),
            html.P(f"Booking ID: {booking_id}", style={'margin': '5px 0', 'fontWeight': '600', 'color': TEXT_PRIMARY}),
            html.P(f"Slot: {slot}", style={'margin': '5px 0', 'color': TEXT_PRIMARY}),
            html.P(f"Estimated Cost: Rs {estimated_cost}",
                   style={'margin': '5px 0', 'color': WARNING_COLOR, 'fontWeight': '600'}),
            html.P("Please note your booking ID for reference",
                   style={'margin': '10px 0 0 0', 'fontSize': '12px', 'color': TEXT_SECONDARY})
        ], style={
            'backgroundColor': MAIN_BG,
            'padding': '20px',
            'borderRadius': '8px',
            'border': f'2px solid {SUCCESS_COLOR}'
        })

    return ''


# ------------------------------
# Admin Dashboard (Keep exactly as original)
# ------------------------------
def render_admin_app(session_data):
    current_user = session_data.get('user', 'Admin')
    current_role = session_data.get('role', 'admin')

    return html.Div([
        # Header
        html.Div([
            html.Div([
                html.Div([
                    html.H1("🅿️ SMART PARKING MANAGEMENT SYSTEM",
                            style={'margin': '0', 'fontSize': '26px', 'fontWeight': '700',
                                   'color': TEXT_PRIMARY, 'letterSpacing': '0.5px'}),
                    html.P("Advanced Real-Time Monitoring & Analytics Platform",
                           style={'margin': '6px 0 0 0', 'fontSize': '13px',
                                  'color': TEXT_SECONDARY, 'fontWeight': '400'})
                ], style={'flex': '1'}),

                html.Div([
                    html.Span(f"👤 {current_user} ({current_role.title()})",
                              style={'color': TEXT_SECONDARY, 'fontSize': '13px', 'marginRight': '20px'}),
                    html.Button(
                        '🚪 Logout',
                        id='logout-button',
                        n_clicks=0,
                        style={**BUTTON_STYLE, 'backgroundColor': DANGER_COLOR, 'padding': '8px 16px',
                               'marginRight': '20px'}
                    ),
                    html.Div([
                        html.Span("●", style={'color': SUCCESS_COLOR, 'fontSize': '16px'}),
                        html.Span("LIVE", style={'color': SUCCESS_COLOR, 'fontSize': '12px', 'fontWeight': '700',
                                                 'letterSpacing': '1px', 'marginLeft': '5px'})
                    ], style={'display': 'inline-flex', 'alignItems': 'center'}),
                ], style={'display': 'flex', 'alignItems': 'center'})
            ], style={'display': 'flex', 'justifyContent': 'space-between', 'alignItems': 'center'})
        ], style={
            'backgroundColor': CARD_BG,
            'padding': '20px 32px',
            'borderRadius': '12px',
            'marginBottom': '20px',
            'border': '1px solid #334155',
            'boxShadow': '0 4px 6px -1px rgba(0, 0, 0, 0.3)'
        }),

        # Tabs
        dcc.Tabs(id='main-tabs', value='dashboard', children=[
            dcc.Tab(label='📊 Dashboard', value='dashboard', style={'backgroundColor': CARD_BG, 'color': TEXT_SECONDARY},
                    selected_style={'backgroundColor': ACCENT_COLOR, 'color': 'white'}),
            dcc.Tab(label='🗺️ Parking Grid', value='grid', style={'backgroundColor': CARD_BG, 'color': TEXT_SECONDARY},
                    selected_style={'backgroundColor': ACCENT_COLOR, 'color': 'white'}),
            dcc.Tab(label='📈 Analytics', value='analytics', style={'backgroundColor': CARD_BG, 'color': TEXT_SECONDARY},
                    selected_style={'backgroundColor': ACCENT_COLOR, 'color': 'white'}),
            dcc.Tab(label='📅 Bookings', value='bookings', style={'backgroundColor': CARD_BG, 'color': TEXT_SECONDARY},
                    selected_style={'backgroundColor': ACCENT_COLOR, 'color': 'white'}),
            dcc.Tab(label='🔔 Alerts', value='alerts', style={'backgroundColor': CARD_BG, 'color': TEXT_SECONDARY},
                    selected_style={'backgroundColor': ACCENT_COLOR, 'color': 'white'}),
            dcc.Tab(label='⚙️ Operations', value='operations',
                    style={'backgroundColor': CARD_BG, 'color': TEXT_SECONDARY},
                    selected_style={'backgroundColor': ACCENT_COLOR, 'color': 'white'}),
        ], style={'marginBottom': '20px'}),

        # Content area
        html.Div(id='tab-content'),

    ], style={
        'backgroundColor': MAIN_BG,
        'padding': '20px',
        'minHeight': '100vh',
        'fontFamily': '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif'
    })


# Logout handler
@app.callback(
    [Output('url', 'pathname', allow_duplicate=True),
     Output('session-store', 'data', allow_duplicate=True)],
    Input('logout-button', 'n_clicks'),
    State('session-store', 'data'),
    prevent_initial_call=True
)
def logout(n_clicks, session_data):
    if n_clicks and n_clicks > 0:
        if session_data:
            log_activity("User Logout", f"User {session_data.get('user', 'Unknown')} logged out",
                         session_data.get('user', 'System'))
        return '/login', {'authenticated': False, 'user': None, 'role': None}
    return dash.no_update, dash.no_update


# Tab content callback (admin dashboard tabs)
@app.callback(
    Output('tab-content', 'children'),
    Input('main-tabs', 'value'),
    Input('interval-component', 'n_intervals')
)
def render_tab_content(tab, n):
    simulate_parking_activity()
    df = get_parking_data()
    stats = get_statistics(df)
    check_alerts(df, stats)

    occupancy_history.append(stats['occupancy_rate'])
    revenue_history.append(stats['total_earnings'])
    timestamp_history.append(datetime.datetime.now())

    if tab == 'dashboard':
        return render_dashboard(df, stats)
    elif tab == 'grid':
        return render_parking_grid(df, stats)
    elif tab == 'analytics':
        return render_analytics(df, stats)
    elif tab == 'bookings':
        return render_bookings(df, stats)
    elif tab == 'alerts':
        return render_alerts_tab(df, stats)
    elif tab == 'operations':
        return render_operations(df, stats)


# All render functions from original code
def render_dashboard(df, stats):
    trend, trend_msg = predict_occupancy()

    gauge_color = SUCCESS_COLOR if stats['occupancy_rate'] < 70 else WARNING_COLOR if stats[
                                                                                          'occupancy_rate'] < 85 else DANGER_COLOR
    gauge_fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=stats['occupancy_rate'],
        number={'suffix': "%", 'font': {'size': 42, 'color': TEXT_PRIMARY}},
        gauge={
            'axis': {'range': [None, 100], 'tickwidth': 2, 'tickcolor': TEXT_SECONDARY},
            'bar': {'color': gauge_color, 'thickness': 0.75},
            'bgcolor': '#1e293b',
            'borderwidth': 2,
            'bordercolor': '#334155',
        }
    ))
    gauge_fig.update_layout(
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        font={'color': TEXT_PRIMARY},
        margin=dict(l=20, r=20, t=20, b=20),
        height=280
    )

    zone_counts = df.groupby(['Zone', 'Status']).size().unstack(fill_value=0)
    zone_fig = go.Figure(data=[
        go.Bar(name='Available', x=zone_counts.index,
               y=zone_counts.get('AVAILABLE', [0] * len(zone_counts)),
               marker_color=SUCCESS_COLOR),
        go.Bar(name='Occupied', x=zone_counts.index,
               y=zone_counts.get('OCCUPIED', [0] * len(zone_counts)),
               marker_color=DANGER_COLOR)
    ])
    zone_fig.update_layout(
        barmode='group',
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        font={'color': TEXT_PRIMARY, 'size': 11},
        xaxis={'showgrid': False},
        yaxis={'showgrid': True, 'gridcolor': '#334155'},
        legend={'orientation': 'h', 'yanchor': 'top', 'y': -0.2},
        margin=dict(l=40, r=20, t=20, b=50),
        height=280,
        showlegend=True
    )

    occupied_df = df[df['Status'] == 'OCCUPIED']
    vehicle_counts = occupied_df['Vehicle'].value_counts()
    vehicle_fig = go.Figure(data=[go.Pie(
        labels=vehicle_counts.index,
        values=vehicle_counts.values,
        hole=0.5,
        marker=dict(colors=[ACCENT_COLOR, SUCCESS_COLOR, WARNING_COLOR]),
        textinfo='label+percent'
    )])
    vehicle_fig.update_layout(
        paper_bgcolor='rgba(0,0,0,0)',
        font={'color': TEXT_PRIMARY, 'size': 11},
        showlegend=True,
        legend={'orientation': 'h', 'yanchor': 'top', 'y': -0.1},
        margin=dict(l=20, r=20, t=20, b=50),
        height=280
    )

    alert_items = [
        html.Div([
            html.Span(alert['icon'], style={'fontSize': '18px', 'marginRight': '10px'}),
            html.Span(alert['message'], style={'flex': '1', 'fontSize': '13px'}),
            html.Span(alert['time'], style={'fontSize': '11px', 'opacity': '0.7'})
        ], style={
            'display': 'flex',
            'alignItems': 'center',
            'padding': '10px',
            'marginBottom': '8px',
            'backgroundColor': MAIN_BG,
            'borderRadius': '6px',
            'color': TEXT_PRIMARY,
            'border': f"1px solid {DANGER_COLOR if alert['type'] == 'critical' else WARNING_COLOR if alert['type'] == 'warning' else SUCCESS_COLOR if alert['type'] == 'success' else INFO_COLOR}"
        })
        for alert in alerts[:5]
    ] if len(alerts) > 0 else [html.P("No active alerts", style={'color': TEXT_SECONDARY, 'fontStyle': 'italic'})]

    display_df = df.drop(columns=['_revenue', '_fine', '_duration_hours', '_is_reserved', '_maintenance'])
    data_table = dash_table.DataTable(
        data=display_df.to_dict('records'),
        columns=[{'name': col, 'id': col} for col in display_df.columns],
        style_table={'overflowX': 'auto'},
        style_cell={
            'textAlign': 'left',
            'padding': '12px 16px',
            'fontFamily': 'Arial',
            'fontSize': '12px',
            'backgroundColor': CARD_BG,
            'color': TEXT_PRIMARY,
            'border': '1px solid #334155'
        },
        style_header={
            'backgroundColor': '#334155',
            'color': TEXT_PRIMARY,
            'fontWeight': '600',
            'fontSize': '11px',
            'letterSpacing': '0.3px'
        },
        style_data_conditional=[
            {
                'if': {'filter_query': '{Status} = "OCCUPIED"', 'column_id': 'Status'},
                'backgroundColor': DANGER_COLOR,
                'color': 'white',
                'fontWeight': '600'
            },
            {
                'if': {'filter_query': '{Status} = "AVAILABLE"', 'column_id': 'Status'},
                'backgroundColor': SUCCESS_COLOR,
                'color': 'white',
                'fontWeight': '600'
            },
            {
                'if': {'row_index': 'odd'},
                'backgroundColor': '#1a2332'
            }
        ],
        page_size=10,
        sort_action='native',
        filter_action='native',
    )

    return html.Div([
        html.Div([
            html.Div([
                html.Div([
                    html.Span("📊", style={'fontSize': '22px'}),
                    html.Span("CAPACITY", style={'fontSize': '11px', 'color': TEXT_SECONDARY, 'fontWeight': '600',
                                                 'letterSpacing': '0.5px'})
                ], style={'display': 'flex', 'justifyContent': 'space-between', 'alignItems': 'center'}),
                html.H2(f"{TOTAL_SLOTS}",
                        style={'color': TEXT_PRIMARY, 'fontSize': '32px', 'fontWeight': '700', 'margin': '10px 0 0 0'})
            ], style={**METRIC_CARD, 'width': '19%', 'display': 'inline-block', 'marginRight': '1%'}),

            html.Div([
                html.Div([
                    html.Span("✅", style={'fontSize': '22px'}),
                    html.Span("AVAILABLE", style={'fontSize': '11px', 'color': TEXT_SECONDARY, 'fontWeight': '600',
                                                  'letterSpacing': '0.5px'})
                ], style={'display': 'flex', 'justifyContent': 'space-between', 'alignItems': 'center'}),
                html.H2(f"{stats['available']}",
                        style={'color': SUCCESS_COLOR, 'fontSize': '32px', 'fontWeight': '700', 'margin': '10px 0 0 0'})
            ], style={**METRIC_CARD, 'width': '19%', 'display': 'inline-block', 'marginRight': '1%'}),

            html.Div([
                html.Div([
                    html.Span("🚗", style={'fontSize': '22px'}),
                    html.Span("OCCUPIED", style={'fontSize': '11px', 'color': TEXT_SECONDARY, 'fontWeight': '600',
                                                 'letterSpacing': '0.5px'})
                ], style={'display': 'flex', 'justifyContent': 'space-between', 'alignItems': 'center'}),
                html.H2(f"{stats['occupied']}",
                        style={'color': DANGER_COLOR, 'fontSize': '32px', 'fontWeight': '700', 'margin': '10px 0 0 0'})
            ], style={**METRIC_CARD, 'width': '19%', 'display': 'inline-block', 'marginRight': '1%'}),

            html.Div([
                html.Div([
                    html.Span("📌", style={'fontSize': '22px'}),
                    html.Span("BOOKINGS", style={'fontSize': '11px', 'color': TEXT_SECONDARY, 'fontWeight': '600',
                                                 'letterSpacing': '0.5px'})
                ], style={'display': 'flex', 'justifyContent': 'space-between', 'alignItems': 'center'}),
                html.H2(f"{len(bookings)}",
                        style={'color': INFO_COLOR, 'fontSize': '32px', 'fontWeight': '700', 'margin': '10px 0 0 0'})
            ], style={**METRIC_CARD, 'width': '19%', 'display': 'inline-block', 'marginRight': '1%'}),

            html.Div([
                html.Div([
                    html.Span("💰", style={'fontSize': '22px'}),
                    html.Span("EARNINGS", style={'fontSize': '11px', 'color': TEXT_SECONDARY, 'fontWeight': '600',
                                                 'letterSpacing': '0.5px'})
                ], style={'display': 'flex', 'justifyContent': 'space-between', 'alignItems': 'center'}),
                html.H2(f"Rs {stats['total_earnings']:,.0f}",
                        style={'color': WARNING_COLOR, 'fontSize': '28px', 'fontWeight': '700', 'margin': '10px 0 0 0'})
            ], style={**METRIC_CARD, 'width': '19%', 'display': 'inline-block'}),
        ], style={'marginBottom': '20px'}),

        html.Div([
            html.Div([
                html.Div([
                    html.Span("🤖 AI Prediction: ",
                              style={'color': TEXT_SECONDARY, 'fontSize': '13px', 'fontWeight': '500'}),
                    html.Span(trend_msg, style={'color': WARNING_COLOR if trend == "increasing" else SUCCESS_COLOR,
                                                'fontSize': '13px', 'fontWeight': '600'})
                ], style={'marginBottom': '8px'}),
                html.Div([
                    html.Span("⏱️ Avg. Wait Time: ",
                              style={'color': TEXT_SECONDARY, 'fontSize': '13px', 'fontWeight': '500'}),
                    html.Span(f"{stats['avg_wait']:.0f} minutes" if stats['avg_wait'] > 0 else "No wait",
                              style={'color': TEXT_PRIMARY, 'fontSize': '13px', 'fontWeight': '600'})
                ])
            ], style={**CHART_CARD, 'padding': '16px 24px', 'width': '24%', 'display': 'inline-block',
                      'marginRight': '1%'}),

            html.Div([
                html.Div([
                    html.Span("📊 Turnover Rate: ",
                              style={'color': TEXT_SECONDARY, 'fontSize': '13px', 'fontWeight': '500'}),
                    html.Span(f"{stats['turnover_rate']:.1f} slots/hour",
                              style={'color': TEXT_PRIMARY, 'fontSize': '13px', 'fontWeight': '600'})
                ], style={'marginBottom': '8px'}),
                html.Div([
                    html.Span("⏳ Avg. Duration: ",
                              style={'color': TEXT_SECONDARY, 'fontSize': '13px', 'fontWeight': '500'}),
                    html.Span(f"{stats['avg_duration']:.1f} hours",
                              style={'color': TEXT_PRIMARY, 'fontSize': '13px', 'fontWeight': '600'})
                ])
            ], style={**CHART_CARD, 'padding': '16px 24px', 'width': '24%', 'display': 'inline-block',
                      'marginRight': '1%'}),

            html.Div([
                html.Div([
                    html.Span("⚠️ Overstays: ",
                              style={'color': TEXT_SECONDARY, 'fontSize': '13px', 'fontWeight': '500'}),
                    html.Span(f"{stats['overstay_count']} vehicles",
                              style={'color': DANGER_COLOR, 'fontSize': '13px', 'fontWeight': '600'})
                ], style={'marginBottom': '8px'}),
                html.Div([
                    html.Span("💸 Fine Collection: ",
                              style={'color': TEXT_SECONDARY, 'fontSize': '13px', 'fontWeight': '500'}),
                    html.Span(f"Rs {stats['total_fines']:,.0f}",
                              style={'color': WARNING_COLOR, 'fontSize': '13px', 'fontWeight': '600'})
                ])
            ], style={**CHART_CARD, 'padding': '16px 24px', 'width': '24%', 'display': 'inline-block',
                      'marginRight': '1%'}),

            html.Div([
                html.Div([
                    html.Span("📈 Occupancy: ",
                              style={'color': TEXT_SECONDARY, 'fontSize': '13px', 'fontWeight': '500'}),
                    html.Span(f"{stats['occupancy_rate']:.1f}%",
                              style={'color': ACCENT_COLOR, 'fontSize': '13px', 'fontWeight': '600'})
                ], style={'marginBottom': '8px'}),
                html.Div([
                    html.Span("🔧 Maintenance: ",
                              style={'color': TEXT_SECONDARY, 'fontSize': '13px', 'fontWeight': '500'}),
                    html.Span(f"{stats['maintenance']} slots",
                              style={'color': TEXT_PRIMARY, 'fontSize': '13px', 'fontWeight': '600'})
                ])
            ], style={**CHART_CARD, 'padding': '16px 24px', 'width': '24%', 'display': 'inline-block'}),
        ], style={'marginBottom': '20px'}),

        html.Div([
            html.Div([
                html.H3("Occupancy Status",
                        style={'color': TEXT_PRIMARY, 'fontSize': '16px', 'fontWeight': '600', 'marginBottom': '16px'}),
                dcc.Graph(figure=gauge_fig, config={'displayModeBar': False}, style={'height': '280px'})
            ], style={**CHART_CARD, 'width': '32%', 'display': 'inline-block', 'marginRight': '1%'}),

            html.Div([
                html.H3("Zone Distribution",
                        style={'color': TEXT_PRIMARY, 'fontSize': '16px', 'fontWeight': '600', 'marginBottom': '16px'}),
                dcc.Graph(figure=zone_fig, config={'displayModeBar': False}, style={'height': '280px'})
            ], style={**CHART_CARD, 'width': '32%', 'display': 'inline-block', 'marginRight': '1%'}),

            html.Div([
                html.H3("Vehicle Types",
                        style={'color': TEXT_PRIMARY, 'fontSize': '16px', 'fontWeight': '600', 'marginBottom': '16px'}),
                dcc.Graph(figure=vehicle_fig, config={'displayModeBar': False}, style={'height': '280px'})
            ], style={**CHART_CARD, 'width': '32%', 'display': 'inline-block'}),
        ]),

        html.Div([
            html.H3("🔔 Recent Alerts",
                    style={'color': TEXT_PRIMARY, 'fontSize': '16px', 'fontWeight': '600', 'marginBottom': '16px'}),
            html.Div(alert_items)
        ], style=CHART_CARD),

        html.Div([
            html.Div([
                html.H3("Parking Slot Details", style={'color': TEXT_PRIMARY, 'fontSize': '16px', 'fontWeight': '600'}),
                html.Div([
                    html.Button("Export CSV", id="export-csv", style=BUTTON_STYLE),
                    html.Button("Refresh Data", id="refresh-data", style=BUTTON_STYLE),
                ], style={'marginTop': '10px'})
            ], style={'display': 'flex', 'justifyContent': 'space-between', 'alignItems': 'center',
                      'marginBottom': '16px'}),
            html.Div(data_table)
        ], style=CHART_CARD),

        html.Div(
            f"Last Updated: {datetime.datetime.now().strftime('%d %B %Y | %I:%M:%S %p')} • Auto-refresh: 30s • System Online • {len(df)} records displayed",
            style={'textAlign': 'center', 'marginTop': '16px', 'color': TEXT_SECONDARY, 'fontSize': '12px',
                   'fontWeight': '500'}),
    ])


def render_parking_grid(df, stats):
    grid_slots = []
    slots_per_row = 10

    for i in range(0, TOTAL_SLOTS, slots_per_row):
        row = []
        for j in range(slots_per_row):
            idx = i + j
            if idx < TOTAL_SLOTS:
                slot_id = f"P{str(idx + 1).zfill(3)}"
                details = parking_data[slot_id]
                status = details['status']

                if details['maintenance']:
                    color = '#64748b'
                    icon = '🔧'
                    status_text = 'MAINT'
                elif details['is_reserved']:
                    color = INFO_COLOR
                    icon = '📌'
                    status_text = 'RESERVED'
                elif status == 'occupied':
                    color = DANGER_COLOR
                    icon = '🚗'
                    status_text = 'OCCUPIED'
                else:
                    color = SUCCESS_COLOR
                    icon = '✅'
                    status_text = 'FREE'

                row.append(
                    html.Div([
                        html.Div(icon, style={'fontSize': '24px', 'marginBottom': '4px'}),
                        html.Div(slot_id, style={'fontSize': '11px', 'fontWeight': '600'}),
                        html.Div(status_text, style={'fontSize': '9px', 'opacity': '0.8'})
                    ], style={
                        'backgroundColor': color,
                        'width': '90px',
                        'height': '90px',
                        'margin': '5px',
                        'borderRadius': '8px',
                        'display': 'flex',
                        'flexDirection': 'column',
                        'justifyContent': 'center',
                        'alignItems': 'center',
                        'cursor': 'pointer',
                        'transition': 'all 0.3s',
                        'color': 'white',
                        'fontWeight': '600',
                        'boxShadow': '0 2px 4px rgba(0,0,0,0.3)'
                    })
                )
        grid_slots.append(html.Div(row, style={'display': 'flex', 'justifyContent': 'center'}))

    return html.Div([
        html.Div([
            html.H2("🗺️ Interactive Parking Grid", style={'color': TEXT_PRIMARY, 'marginBottom': '10px'}),
            html.P(f"Real-time visualization of all {TOTAL_SLOTS} parking slots",
                   style={'color': TEXT_SECONDARY, 'marginBottom': '20px'})
        ]),

        html.Div([
            html.Div([
                html.Span("✅", style={'fontSize': '20px', 'marginRight': '5px'}),
                html.Span("Available", style={'color': TEXT_PRIMARY})
            ], style={'display': 'inline-block', 'marginRight': '20px'}),
            html.Div([
                html.Span("🚗", style={'fontSize': '20px', 'marginRight': '5px'}),
                html.Span("Occupied", style={'color': TEXT_PRIMARY})
            ], style={'display': 'inline-block', 'marginRight': '20px'}),
            html.Div([
                html.Span("📌", style={'fontSize': '20px', 'marginRight': '5px'}),
                html.Span("Reserved", style={'color': TEXT_PRIMARY})
            ], style={'display': 'inline-block', 'marginRight': '20px'}),
            html.Div([
                html.Span("🔧", style={'fontSize': '20px', 'marginRight': '5px'}),
                html.Span("Maintenance", style={'color': TEXT_PRIMARY})
            ], style={'display': 'inline-block'})
        ], style={**CHART_CARD, 'padding': '15px', 'textAlign': 'center', 'marginBottom': '20px'}),

        html.Div(grid_slots, style={**CHART_CARD, 'padding': '20px'})
    ])


def render_analytics(df, stats):
    if len(timestamp_history) > 1:
        time_df = pd.DataFrame({
            'Time': list(timestamp_history),
            'Occupancy': list(occupancy_history),
            'Revenue': list(revenue_history)
        })

        occupancy_trend = go.Figure()
        occupancy_trend.add_trace(go.Scatter(
            x=time_df['Time'], y=time_df['Occupancy'],
            mode='lines+markers',
            line=dict(color=ACCENT_COLOR, width=3),
            fill='tozeroy',
            fillcolor='rgba(59, 130, 246, 0.1)'
        ))
        occupancy_trend.update_layout(
            title="Occupancy Trend",
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            font={'color': TEXT_PRIMARY},
            xaxis={'showgrid': True, 'gridcolor': '#334155'},
            yaxis={'showgrid': True, 'gridcolor': '#334155', 'title': 'Occupancy %'},
            margin=dict(l=50, r=20, t=40, b=40),
            height=300
        )

        revenue_trend = go.Figure()
        revenue_trend.add_trace(go.Scatter(
            x=time_df['Time'], y=time_df['Revenue'],
            mode='lines+markers',
            line=dict(color=WARNING_COLOR, width=3),
            fill='tozeroy',
            fillcolor='rgba(245, 158, 11, 0.1)'
        ))
        revenue_trend.update_layout(
            title="Revenue Trend",
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            font={'color': TEXT_PRIMARY},
            xaxis={'showgrid': True, 'gridcolor': '#334155'},
            yaxis={'showgrid': True, 'gridcolor': '#334155', 'title': 'Revenue (Rs)'},
            margin=dict(l=50, r=20, t=40, b=40),
            height=300
        )
    else:
        occupancy_trend = go.Figure()
        revenue_trend = go.Figure()

    hours = list(range(24))
    days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    heatmap_data = np.random.randint(30, 95, size=(7, 24))

    for i in range(7):
        for start, end in PEAK_HOURS:
            heatmap_data[i, start:end] = np.random.randint(75, 95, size=(end - start,))

    heatmap = go.Figure(data=go.Heatmap(
        z=heatmap_data,
        x=hours,
        y=days,
        colorscale='RdYlGn_r',
        text=heatmap_data,
        texttemplate='%{text}%',
        textfont={"size": 10},
        colorbar=dict(title="Occupancy %")
    ))
    heatmap.update_layout(
        title="Weekly Occupancy Heatmap",
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',
        font={'color': TEXT_PRIMARY},
        xaxis={'title': 'Hour of Day'},
        yaxis={'title': 'Day of Week'},
        height=350
    )

    return html.Div([
        html.H2("📈 Advanced Analytics", style={'color': TEXT_PRIMARY, 'marginBottom': '20px'}),

        html.Div([
            html.Div([
                dcc.Graph(figure=occupancy_trend, config={'displayModeBar': False})
            ], style={**CHART_CARD, 'width': '49%', 'display': 'inline-block', 'marginRight': '1%'}),

            html.Div([
                dcc.Graph(figure=revenue_trend, config={'displayModeBar': False})
            ], style={**CHART_CARD, 'width': '49%', 'display': 'inline-block'}),
        ]),

        html.Div([
            dcc.Graph(figure=heatmap, config={'displayModeBar': False})
        ], style=CHART_CARD),

        html.Div([
            html.H3("📊 Statistical Insights", style={'color': TEXT_PRIMARY, 'marginBottom': '15px'}),
            html.Div([
                html.Div([
                    html.Strong("Peak Hour:", style={'color': TEXT_SECONDARY}),
                    html.Span(" 8:00 AM - 10:00 AM & 5:00 PM - 8:00 PM", style={'color': TEXT_PRIMARY})
                ], style={'marginBottom': '10px'}),
                html.Div([
                    html.Strong("Average Daily Revenue:", style={'color': TEXT_SECONDARY}),
                    html.Span(f" Rs {stats['total_earnings'] * 2:,.0f} (projected)", style={'color': WARNING_COLOR})
                ], style={'marginBottom': '10px'}),
                html.Div([
                    html.Strong("Utilization Rate:", style={'color': TEXT_SECONDARY}),
                    html.Span(f" {stats['occupancy_rate']:.1f}%", style={'color': ACCENT_COLOR})
                ], style={'marginBottom': '10px'}),
                html.Div([
                    html.Strong("Busiest Zone:", style={'color': TEXT_SECONDARY}),
                    html.Span(" Zone-A (Closest to entrance)", style={'color': TEXT_PRIMARY})
                ])
            ])
        ], style=CHART_CARD)
    ])


def render_bookings(df, stats):
    return html.Div([
        html.H2("📅 Booking Management", style={'color': TEXT_PRIMARY, 'marginBottom': '20px'}),

        html.Div([
            html.H3("📋 Active Bookings", style={'color': TEXT_PRIMARY, 'marginBottom': '15px'}),
            html.Div([
                         html.P("No active bookings", style={'color': TEXT_SECONDARY, 'fontStyle': 'italic'})
                     ] if len(bookings) == 0 else [
                html.Div([
                    html.Div([
                        html.Span("🎫", style={'fontSize': '20px', 'marginRight': '10px'}),
                        html.Div([
                            html.Div(f"Booking ID: {b['id']}", style={'fontWeight': '600', 'color': TEXT_PRIMARY}),
                            html.Div(f"Customer: {b['name']} | Phone: {b['phone']}",
                                     style={'color': TEXT_SECONDARY, 'fontSize': '13px', 'marginTop': '4px'}),
                            html.Div(f"Slot: {b['slot']} | Vehicle: {b['vehicle']} ({b['license']})",
                                     style={'color': TEXT_SECONDARY, 'fontSize': '13px', 'marginTop': '2px'}),
                            html.Div(f"Duration: {b['duration']}hrs | Cost: Rs {b['cost']} | Time: {b['timestamp']}",
                                     style={'color': TEXT_SECONDARY, 'fontSize': '13px', 'marginTop': '2px'}),
                            html.Div(f"Status: {b.get('status', 'active').upper()}",
                                     style={'color': SUCCESS_COLOR if b.get('status',
                                                                            'active') == 'active' else TEXT_SECONDARY,
                                            'fontSize': '13px', 'marginTop': '2px', 'fontWeight': '600'})
                        ], style={'flex': '1'})
                    ], style={'display': 'flex', 'alignItems': 'start'})
                ], style={'padding': '15px', 'backgroundColor': MAIN_BG, 'borderRadius': '6px',
                          'marginBottom': '10px', 'border': f'1px solid {INFO_COLOR}'})
                for b in bookings
            ])
        ], style=CHART_CARD),
    ])


def render_alerts_tab(df, stats):
    return html.Div([
        html.H2("🔔 Alerts & Notifications", style={'color': TEXT_PRIMARY, 'marginBottom': '20px'}),

        html.Div([
            html.H3("🚨 Active Alerts", style={'color': TEXT_PRIMARY, 'marginBottom': '15px'}),
            html.Div([
                         html.Div([
                             html.Span(alert['icon'], style={'fontSize': '20px', 'marginRight': '10px'}),
                             html.Span(alert['message'], style={'color': TEXT_PRIMARY, 'flex': '1'}),
                             html.Span(alert['time'], style={'color': TEXT_SECONDARY, 'fontSize': '12px'})
                         ], style={
                             'display': 'flex',
                             'alignItems': 'center',
                             'padding': '12px',
                             'marginBottom': '10px',
                             'backgroundColor': MAIN_BG,
                             'borderRadius': '6px',
                             'border': f"1px solid {DANGER_COLOR if alert['type'] == 'critical' else WARNING_COLOR if alert['type'] == 'warning' else SUCCESS_COLOR if alert['type'] == 'success' else INFO_COLOR}"
                         })
                         for alert in alerts
                     ] if len(alerts) > 0 else [
                html.P("No active alerts", style={'color': TEXT_SECONDARY, 'fontStyle': 'italic'})
            ])
        ], style=CHART_CARD),
    ])


def render_operations(df, stats):
    return html.Div([
        html.H2("⚙️ Operations & Management", style={'color': TEXT_PRIMARY, 'marginBottom': '20px'}),

        html.Div([
            html.H3("📋 Activity Log", style={'color': TEXT_PRIMARY, 'marginBottom': '15px'}),
            html.Div([
                         html.Div([
                             html.Span(f"[{log['timestamp']}]",
                                       style={'color': TEXT_SECONDARY, 'fontSize': '12px', 'marginRight': '10px'}),
                             html.Span(f"{log['user']}: ",
                                       style={'color': ACCENT_COLOR, 'fontWeight': '600', 'marginRight': '5px'}),
                             html.Span(f"{log['action']} - {log['details']}", style={'color': TEXT_PRIMARY})
                         ], style={'marginBottom': '8px', 'fontSize': '13px'})
                         for log in list(activity_log)[-20:]
                     ] if len(activity_log) > 0 else [
                html.P("No recent activities", style={'color': TEXT_SECONDARY, 'fontStyle': 'italic'})
            ])
        ], style=CHART_CARD),

        html.Div([
            html.H3("🖥️ System Status", style={'color': TEXT_PRIMARY, 'marginBottom': '15px'}),
            html.Div([
                html.Div([
                    html.Span("API Status:",
                              style={'color': TEXT_SECONDARY, 'width': '150px', 'display': 'inline-block'}),
                    html.Span("🟢 Online", style={'color': SUCCESS_COLOR, 'fontWeight': '600'})
                ], style={'marginBottom': '10px'}),
                html.Div([
                    html.Span("Database:",
                              style={'color': TEXT_SECONDARY, 'width': '150px', 'display': 'inline-block'}),
                    html.Span("🟢 Connected", style={'color': SUCCESS_COLOR, 'fontWeight': '600'})
                ], style={'marginBottom': '10px'}),
                html.Div([
                    html.Span("Total Bookings:",
                              style={'color': TEXT_SECONDARY, 'width': '150px', 'display': 'inline-block'}),
                    html.Span(f"{len(bookings)}", style={'color': TEXT_PRIMARY, 'fontWeight': '600'})
                ])
            ])
        ], style=CHART_CARD),
    ])


# ------------------------------
# Run App
# ------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)