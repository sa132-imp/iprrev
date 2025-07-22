from flask import Flask, render_template, request, redirect, url_for, session, flash
from datetime import datetime, date
import pandas as pd
import sqlite3
import os
import io
import csv
from functools import wraps

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'  # Change this to a secure random string

# ====== CONFIG ======
PASSWORD = "letmein"  # Change this to whatever password you want
DB_PATH = "form_data_web.db"

# ====== Load Config Files ======
field_configs = pd.read_csv("Form_Requirements_Template.csv")
field_configs.columns = [col.strip() for col in field_configs.columns]
field_configs["Field Name"] = field_configs["Field Name"].str.strip()
field_configs["Column"] = pd.to_numeric(field_configs["Column"], errors="coerce").fillna(1).astype(int)
field_configs = field_configs.to_dict(orient="records")

trusts_df = pd.read_csv("trusts.csv")
trusts_df['Trust name'] = trusts_df['Trust name'].str.strip().str.rstrip(',')
trust_names = sorted(trusts_df['Trust name'].tolist())

# ====== LOGIN REQUIRED DECORATOR ======
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function

# ====== ROUTES ======
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.form.get('password') == PASSWORD:
            session['logged_in'] = True
            return redirect(url_for('form'))
        else:
            flash('Incorrect password. Please try again.')
            return redirect(url_for('login'))
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/set_reviewer', methods=['POST'])
@login_required
def set_reviewer():
    session['reviewer'] = request.form.get('reviewer')
    session.pop('selected_trust', None)
    session.pop('edit_mode', None)
    return redirect(url_for('form'))

@app.route('/select_trust/<trust_name>')
@login_required
def select_trust(trust_name):
    session['selected_trust'] = trust_name
    session.pop('edit_mode', None)
    return redirect(url_for('form'))

@app.route('/edit_trust/<trust_name>', methods=['POST'])
@login_required
def edit_trust(trust_name):
    session['selected_trust'] = trust_name
    session['edit_mode'] = True
    return redirect(url_for('form'))

@app.route('/cancel_edit')
@login_required
def cancel_edit():
    session['edit_mode'] = False
    return redirect(url_for('form'))

@app.route('/', methods=['GET', 'POST'])
@login_required
def form():
    current_date = date.today().isoformat()
    reviewer = session.get('reviewer')
    selected_trust = session.get('selected_trust')
    submitted_data = None
    trust_data = {}

    edit_mode = request.form.get('edit_mode') == 'true' or session.get('edit_mode', False)
    read_only = False
    input_mode = False

    to_do_list = []
    done_list = []

    if request.method == 'POST':
        trust_name = request.form.get("TrustName")
        if not trust_name:
            flash("Trust name is missing.")
            return redirect(url_for('form'))

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        data = {}
        for config in field_configs:
            field = config['Field Name']
            value = request.form.getlist(field) if field == 'Additional' else request.form.get(field, '')
            if isinstance(value, list):
                value = ", ".join(value)
            data[field] = value

        submission_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        data['SubmissionTimestamp'] = submission_timestamp
        data['EditReason'] = request.form.get('EditReason', '').strip() if edit_mode else ''

        fields = ", ".join(data.keys())
        placeholders = ", ".join("?" for _ in data)
        values = list(data.values())

        sql = f"INSERT INTO submissions ({fields}) VALUES ({placeholders})"
        cursor.execute(sql, values)

        cursor.execute("""
            UPDATE trust_details
            SET CompletedBy = ?, CompletedTimestamp = ?
            WHERE TrustName = ?
        """, (reviewer, submission_timestamp, trust_name))

        conn.commit()
        conn.close()

        session['submitted_data'] = data
        session['selected_trust'] = trust_name
        session.pop('edit_mode', None)
        return redirect(url_for('form'))

    if reviewer:
        conn = sqlite3.connect(DB_PATH)
        df = pd.read_sql_query("SELECT * FROM trust_details WHERE Assigned = ?", conn, params=(reviewer,))
        conn.close()

        to_do_list = df[df['CompletedBy'].isna()]['TrustName'].tolist()
        done_list = df[df['CompletedBy'].notna()]['TrustName'].tolist()

        if selected_trust:
            selected_row = df[df['TrustName'] == selected_trust]
            if not selected_row.empty:
                trust_data = selected_row.iloc[0].to_dict()

                if selected_trust in done_list:
                    conn = sqlite3.connect(DB_PATH)
                    sub_df = pd.read_sql_query(
                        "SELECT * FROM submissions WHERE TrustName = ? ORDER BY SubmissionTimestamp DESC LIMIT 1",
                        conn, params=(selected_trust,))
                    conn.close()
                    if not sub_df.empty:
                        trust_data.update(sub_df.iloc[0].to_dict())
                        read_only = not edit_mode
                else:
                    input_mode = True

    if 'submitted_data' in session:
        submitted_data = session.pop('submitted_data')

    return render_template('form.html',
                           field_configs=field_configs,
                           trusts=trust_names,
                           current_date=current_date,
                           stats=[],
                           edit_mode=edit_mode,
                           read_only=read_only,
                           input_mode=input_mode,
                           submitted_data=submitted_data,
                           reviewer=reviewer,
                           to_do_list=to_do_list,
                           done_list=done_list,
                           selected_trust=selected_trust,
                           trust_data=trust_data)

@app.route('/download')
@login_required
def download_csv():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM submissions ORDER BY SubmissionTimestamp DESC")
    rows = cursor.fetchall()
    headers = [description[0] for description in cursor.description]
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows(rows)

    output.seek(0)
    return (
        output.getvalue(),
        200,
        {
            "Content-Type": "text/csv",
            "Content-Disposition": "attachment; filename=submissions.csv"
        }
    )

# ====== RENDER.COM COMPAT ======
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
