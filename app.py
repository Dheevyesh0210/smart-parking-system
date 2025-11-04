def get_parking_data():
    try:
        # Create SQLAlchemy engine for PostgreSQL compatibility
        engine = create_engine(DATABASE_URL)
        
        query = '''SELECT slot_id, zone, status, entry_time, vehicle_type, 
                   license_plate, customer_id, is_reserved, maintenance
                   FROM parking_slots ORDER BY slot_id'''
        
        # Read data using SQLAlchemy engine
        df = pd.read_sql_query(query, engine)
        engine.dispose()
        
        print(f"=" * 80)
        print(f"=== DEBUG get_parking_data() ===")
        print(f"Rows from database: {len(df)}")
        if not df.empty:
            print(f"Status values from DB: {df['status'].unique()}")
            print(f"Maintenance values from DB: {df['maintenance'].unique()}")
            print(f"Maintenance TRUE count: {df['maintenance'].sum()}")
            print(f"First row: {df.iloc[0].to_dict()}")
        print(f"=" * 80)

        rows = []
        for _, row in df.iterrows():
            fine = 0
            revenue = 0
            duration_str = "-"
            duration_hours = 0

            if row['maintenance']:
                status = "MAINTENANCE"
            else:
                status = str(row['status']).upper() if row['status'] else 'AVAILABLE'

            if status == 'OCCUPIED' and row['entry_time']:
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
                "Status": status,
                "Vehicle": row['vehicle_type'] if status == 'OCCUPIED' else "-",
                "License": row['license_plate'] if status == 'OCCUPIED' else "-",
                "Customer ID": row['customer_id'] if status == 'OCCUPIED' else "-",
                "Entry Time": str(row['entry_time']) if row['entry_time'] else "-",
                "Duration": duration_str,
                "Rate": f"Rs {get_dynamic_rate()}/hr" if status == 'OCCUPIED' else "-",
                "Parking Fee": f"Rs {revenue:.0f}",
                "Overstay Fine": f"Rs {fine:.0f}",
                "Total": f"Rs {(revenue + fine):.0f}",
                "_revenue": revenue,
                "_fine": fine,
                "_duration_hours": duration_hours,
                "_is_reserved": row['is_reserved'],
                "_maintenance": row['maintenance']
            })

        result_df = pd.DataFrame(rows)
        print(f"=== RESULT ===")
        print(f"Returning {len(result_df)} rows")
        if not result_df.empty:
            print(f"Final Status counts: {result_df['Status'].value_counts().to_dict()}")
        print(f"=" * 80)

        return result_df

    except Exception as e:
        print(f"Get parking data error: {e}")
        import traceback
        traceback.print_exc()
        return pd.DataFrame()
