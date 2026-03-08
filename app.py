import streamlit as st
import psycopg2
from psycopg2 import pool
import pandas as pd
import plotly.express as px
import altair as alt
from datetime import datetime, timedelta
import time
from streamlit_autorefresh import st_autorefresh
import geopandas as gpd
from plotly.subplots import make_subplots
import plotly.graph_objects as go
from fpdf import FPDF
import base64
from io import BytesIO
import plotly.io as pio
import zipfile

# Create a connection pool
@st.cache_resource
def init_connection_pool():
    try:
        return psycopg2.pool.SimpleConnectionPool(
            minconn=1,
            maxconn=10,
            host="localhost",
            database="voting",
            user="postgres",
            password="postgres"
        )
    except Exception as e:
        st.error(f"Failed to create connection pool: {e}")
        return None

# Function to get a connection from the pool
def get_db_conn():
    try:
        return init_connection_pool().getconn()
    except Exception as e:
        st.error(f"Failed to get database connection: {e}")
        return None

# Function to return a connection to the pool
def put_db_conn(conn):
    try:
        init_connection_pool().putconn(conn)
    except Exception as e:
        st.error(f"Failed to return connection to pool: {e}")

# Function to execute query and return DataFrame
def execute_query(query):
    conn = None
    try:
        conn = get_db_conn()
        if conn:
            return pd.read_sql_query(query, conn)
        return pd.DataFrame()
    except Exception as e:
        st.error(f"Query execution error: {e}")
        return pd.DataFrame()
    finally:
        if conn:
            put_db_conn(conn)

# Modified data fetching functions
@st.cache_data(ttl=30)
def get_total_votes():
    df = execute_query("""
        SELECT 
            COUNT(*) as total_votes,
            MAX(voted_at) as last_update,
            COUNT(*) - LAG(COUNT(*)) OVER (ORDER BY DATE_TRUNC('hour', voted_at)) as hourly_change
        FROM vote
        GROUP BY DATE_TRUNC('hour', voted_at)
        ORDER BY DATE_TRUNC('hour', voted_at) DESC
        LIMIT 1
    """)
    
    if not df.empty:
        return df.iloc[0]['total_votes'], df.iloc[0]['last_update'], df.iloc[0]['hourly_change']
    return 0, None, 0

@st.cache_data(ttl=30)
def get_votes_by_candidate():
    return execute_query("""
        WITH hourly_votes AS (
            SELECT 
                c.candidate_id,
                DATE_TRUNC('hour', v.voted_at) as hour,
                COUNT(*) as hourly_count
            FROM vote v
            JOIN candidate c ON v.candidate_id = c.candidate_id
            GROUP BY c.candidate_id, DATE_TRUNC('hour', v.voted_at)
        ),
        vote_changes AS (
            SELECT 
                candidate_id,
                hourly_count - LAG(hourly_count) OVER (
                    PARTITION BY candidate_id 
                    ORDER BY hour
                ) as hourly_change
            FROM hourly_votes
            ORDER BY hour DESC
            LIMIT 1
        )
        SELECT 
            c.first_name, 
            c.last_name, 
            c.party, 
            COUNT(*) as vote_count,
            ROUND(COUNT(*) * 100.0 / (SELECT COUNT(*) FROM vote), 2) as percentage,
            ROW_NUMBER() OVER (ORDER BY COUNT(*) DESC) as rank,
            COALESCE(vc.hourly_change, 0) as hourly_change
        FROM vote v
        JOIN candidate c ON v.candidate_id = c.candidate_id
        LEFT JOIN vote_changes vc ON vc.candidate_id = c.candidate_id
        GROUP BY 
            c.candidate_id, 
            c.first_name, 
            c.last_name, 
            c.party,
            vc.hourly_change
        ORDER BY vote_count DESC
    """)

@st.cache_data(ttl=30)
def get_historical_trends():
    return execute_query("""
        WITH cumulative_votes AS (
            SELECT 
                c.first_name || ' ' || c.last_name as candidate_name,
                c.party,
                v.voted_at,
                COUNT(*) OVER (
                    PARTITION BY c.candidate_id 
                    ORDER BY v.voted_at
                    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                ) as cumulative_votes
            FROM vote v
            JOIN candidate c ON v.candidate_id = c.candidate_id
            ORDER BY v.voted_at
        )
        SELECT 
            DATE_TRUNC('minute', voted_at) as vote_time,
            candidate_name,
            party,
            MAX(cumulative_votes) as total_votes
        FROM cumulative_votes
        GROUP BY DATE_TRUNC('minute', voted_at), candidate_name, party
        ORDER BY vote_time
    """)

@st.cache_data(ttl=30)
def get_geographical_data():
    votes_by_state = execute_query("""
        SELECT 
            v.address_state, 
            COUNT(*) as vote_count,
            string_agg(DISTINCT c.party, ', ') as parties
        FROM vote vt
        JOIN voter v ON vt.voter_id = v.voter_id
        JOIN candidate c ON vt.candidate_id = c.candidate_id
        GROUP BY v.address_state
    """)
    
    leading_party = execute_query("""
        WITH state_party_votes AS (
            SELECT 
                v.address_state,
                c.party,
                COUNT(*) as party_votes,
                RANK() OVER (PARTITION BY v.address_state ORDER BY COUNT(*) DESC) as rank
            FROM vote vt
            JOIN voter v ON vt.voter_id = v.voter_id
            JOIN candidate c ON vt.candidate_id = c.candidate_id
            GROUP BY v.address_state, c.party
        )
        SELECT 
            address_state,
            party,
            party_votes
        FROM state_party_votes
        WHERE rank = 1
    """)
    
    return votes_by_state, leading_party

@st.cache_data(ttl=30)
def get_demographic_data():
    gender_data = execute_query("""
        SELECT 
            v.gender, 
            COUNT(*) as vote_count,
            ROUND(COUNT(*) * 100.0 / (SELECT COUNT(*) FROM vote), 2) as percentage
        FROM vote vt
        JOIN voter v ON vt.voter_id = v.voter_id
        GROUP BY v.gender
    """)
    
    age_data = execute_query("""
        SELECT 
            CASE 
                WHEN age < 30 THEN '18-29'
                WHEN age < 45 THEN '30-44'
                WHEN age < 60 THEN '45-59'
                ELSE '60+'
            END as age_group,
            COUNT(*) as count,
            ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (), 2) as percentage
        FROM vote vt
        JOIN voter v ON vt.voter_id = v.voter_id
        GROUP BY 
            CASE 
                WHEN age < 30 THEN '18-29'
                WHEN age < 45 THEN '30-44'
                WHEN age < 60 THEN '45-59'
                ELSE '60+'
            END
        ORDER BY age_group
    """)
    
    return gender_data, age_data

@st.cache_data(ttl=30)
def get_candidate_info():
    candidate_data = execute_query("""
    SELECT first_name, last_name, party, age, gender, biography, img_url
    FROM candidate
    """)
    return candidate_data

def get_state_voting_details():
    df= execute_query("""
    WITH state_votes AS (
        SELECT 
            v.address_state,
            c.party,
            COUNT(*) as votes,
            ROUND(AVG(v.age), 1) as avg_age,
            ROUND(100.0 * COUNT(CASE WHEN v.gender = 'male' THEN 1 END) / COUNT(*), 1) as male_pct
        FROM vote vt
        JOIN voter v ON vt.voter_id = v.voter_id
        JOIN candidate c ON vt.candidate_id = c.candidate_id
        GROUP BY v.address_state, c.party
    )
    SELECT 
        s.address_state as "State",
        COALESCE(sv1.votes, 0) as "Management Party",
        COALESCE(sv2.votes, 0) as "Liberation Party",
        COALESCE(sv3.votes, 0) as "United Republic Party",
        COALESCE(sv1.votes, 0) + COALESCE(sv2.votes, 0) + COALESCE(sv3.votes, 0) as "Total Votes",
        ROUND(AVG(COALESCE(sv1.avg_age, 0) + COALESCE(sv2.avg_age, 0) + COALESCE(sv3.avg_age, 0)) / 3, 1) as "Avg Age",
        ROUND(AVG(COALESCE(sv1.male_pct, 0) + COALESCE(sv2.male_pct, 0) + COALESCE(sv3.male_pct, 0)) / 3, 1) as "Male %"
    FROM (SELECT DISTINCT address_state FROM voter) s
    LEFT JOIN state_votes sv1 ON s.address_state = sv1.address_state AND sv1.party = 'Management Party'
    LEFT JOIN state_votes sv2 ON s.address_state = sv2.address_state AND sv2.party = 'Liberation Party'
    LEFT JOIN state_votes sv3 ON s.address_state = sv3.address_state AND sv3.party = 'United Republic Party'
    GROUP BY 
        s.address_state,
        sv1.votes, sv2.votes, sv3.votes
    ORDER BY s.address_state;
    """)
    return df

# Define page config and constants
st.set_page_config(
    page_title="Real-time Voting Dashboard",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Auto refresh every 30 seconds
st_autorefresh(interval=30000, limit=None, key="refresh")

# Define consistent colors for parties
PARTY_COLORS = {
    'Liberation Party': '#0072C6',    # Blue
    'United Republic Party': '#FF4444',  # Red
    'Management Party': '#87CEEB'     # Light Blue
}

def generate_dashboard_pdf():
    try:
        class VotingDashboardPDF(FPDF):
            def header(self):
                self.set_font('Arial', 'B', 15)
                self.cell(0, 10, 'Real-time Voting Dashboard', 0, 1, 'C')
                self.ln(5)

            def footer(self):
                self.set_y(-15)
                self.set_font('Arial', 'I', 8)
                self.cell(0, 10, f'Generated on {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}', 0, 0, 'C')

        # Create PDF object
        pdf = VotingDashboardPDF()
        
        # Add first page
        pdf.add_page()
        
        # Add summary metrics
        pdf.set_font('Arial', '', 12)
        total_votes, last_update, _ = get_total_votes()
        pdf.cell(0, 10, f'Total Votes: {total_votes:,}', 0, 1)
        pdf.cell(0, 10, f'Last Updated: {last_update}', 0, 1)
        pdf.ln(5)

        # Add vote distribution
        votes_by_candidate = get_votes_by_candidate()
        if not votes_by_candidate.empty:
            pdf.set_font('Arial', 'B', 14)
            pdf.cell(0, 10, 'Vote Distribution by Party', 0, 1)
            
            pdf.set_font('Arial', '', 10)
            for _, row in votes_by_candidate.iterrows():
                pdf.cell(0, 8, 
                    f"{row['first_name']} {row['last_name']} ({row['party']}): {row['vote_count']:,} votes ({row['percentage']}%)", 
                    0, 1)
            pdf.ln(5)

        # Add state-level information
        pdf.add_page()
        pdf.set_font('Arial', 'B', 14)
        pdf.cell(0, 10, 'State-Level Voting Details', 0, 1)
        
        state_data = get_state_voting_details()
        if not state_data.empty:
            pdf.set_font('Arial', '', 9)
            col_width = 36
            row_height = 7
            
            # Table headers
            headers = ['State', 'Total Votes', 'Lib. Party', 'Rep. Party', 'Mgt. Party']
            for header in headers:
                pdf.cell(col_width, row_height, header, 1, 0, 'C')
            pdf.ln()
            
            # Table data
            for _, row in state_data.iterrows():
                pdf.cell(col_width, row_height, str(row['State'])[:15], 1)
                pdf.cell(col_width, row_height, str(row['Total Votes']), 1)
                pdf.cell(col_width, row_height, str(row['Liberation Party']), 1)
                pdf.cell(col_width, row_height, str(row['United Republic Party']), 1)
                pdf.cell(col_width, row_height, str(row['Management Party']), 1)
                pdf.ln()

        # Save the PDF to a temporary file
        temp_pdf = "temp_dashboard.pdf"
        pdf.output(temp_pdf)
        
        # Read the temporary file and return its contents
        with open(temp_pdf, 'rb') as file:
            pdf_data = file.read()
        
        # Remove the temporary file
        import os
        os.remove(temp_pdf)
        
        return pdf_data

    except Exception as e:
        st.error(f"Error generating PDF: {str(e)}")
        return None

# download button code
def create_download_buttons():
    st.sidebar.markdown("### Download Options")
    
    # Get data outside the button callbacks
    votes_data = get_votes_by_candidate()
    
    # CSV Download - Direct download
    csv_data = votes_data.to_csv(index=False).encode('utf-8') if not votes_data.empty else None
    st.sidebar.download_button(
        label="Download Data (CSV)",
        data=csv_data if csv_data else "No data available",
        file_name=f"voting_data_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
        mime="text/csv",
        disabled=votes_data.empty,
        type="primary"
    )

    # PDF Download - Direct download
    try:
        pdf_data = generate_dashboard_pdf()
        st.sidebar.download_button(
            label="Download Data (PDF)",
            data=pdf_data if pdf_data else "No data available",
            file_name=f"voting_dashboard_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf",
            mime="application/pdf",
            disabled=pdf_data is None,
            type="secondary"
        )
    except Exception as e:
        st.sidebar.error(f"Error preparing PDF: {str(e)}")
        
def main():
    # Sidebar
    with st.sidebar:
        st.title("Dashboard Controls")
        
        # Time range filter
        time_filter = st.selectbox(
            "Time Range",
            ["Last Hour", "Last 3 Hours", "Last 6 Hours", "All Time"],
            index=3
        )
        
        # Add simplified download buttons
        create_download_buttons()

    # Main content
    st.title('🗳️ Real-time Voting Dashboard')
    st.write("Live voting results updated every 30 seconds")

    # Top metrics row
    col1, col2, col3, col4 = st.columns(4)
    
    with st.spinner("Loading metrics..."):
        total_votes, last_update, hourly_change = get_total_votes()
        votes_by_candidate = get_votes_by_candidate()
        
        # Total Votes
        with col1:
            st.metric(
                "Total Votes Cast",
                f"{total_votes:,}",
                delta=f"{hourly_change:+,} in last hour" if hourly_change else None
            )
        
        # Leading Candidate
        # In the metrics section
        with col2:
            if not votes_by_candidate.empty:
                leader = votes_by_candidate.iloc[0]
                # Create two columns for image and info
                img_col, info_col = st.columns([1, 4])
                
                with img_col:
                    # Get candidate image
                    candidate_info = execute_query(f"""
                        SELECT img_url 
                        FROM candidate 
                        WHERE first_name = '{leader['first_name']}' 
                        AND last_name = '{leader['last_name']}'
                    """)
                    if not candidate_info.empty and candidate_info.iloc[0]['img_url']:
                        st.image(candidate_info.iloc[0]['img_url'], width=50)
                
                with info_col:
                    st.metric(
                        "Leading Candidate",
                        f"{leader['first_name']} {leader['last_name']}",
                        f"{leader['party']} ({leader['percentage']}%)"
                    )
                    # Add vote count below metric
                    st.write(f"{leader['vote_count']} votes")
                    
        
        # Active States
        with col3:
            votes_by_state, _ = get_geographical_data()
            active_states = len(votes_by_state) if not votes_by_state.empty else 0
            st.metric(
                "Active States",
                active_states,
                "Currently Voting"
            )
        
        # Last Updated
        with col4:
            st.metric(
                "Last Updated",
                datetime.now().strftime('%H:%M:%S'),
                datetime.now().strftime('%Y-%m-%d')
            )

    # Vote Distribution and Candidate Performance
    st.markdown("---")
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader('Vote Distribution by Party')
        if not votes_by_candidate.empty:
            party_data = votes_by_candidate.groupby('party')['vote_count'].sum().reset_index()
            fig = px.pie(
                party_data,
                values='vote_count',
                names='party',
                hole=0.3,
                color='party',
                color_discrete_map=PARTY_COLORS
            )
            fig.update_traces(textinfo='percent+label')
            fig.update_layout(
                height=330,
                margin=dict(t=0, b=0, l=0, r=0),
                showlegend=True
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.warning("No party distribution data available")
    
    with col2:
        st.subheader('Votes by Candidate')
        if not votes_by_candidate.empty:
            bar_chart = alt.Chart(votes_by_candidate).mark_bar().encode(
                x=alt.X('vote_count:Q', title='Number of Votes'),
                y=alt.Y('first_name:N', title='Candidate', sort='-x'),
                color=alt.Color(
                    'party:N',
                    scale=alt.Scale(domain=list(PARTY_COLORS.keys()),
                                  range=list(PARTY_COLORS.values()))
                ),
                tooltip=['first_name', 'last_name', 'party', 'vote_count', 'percentage']
            ).properties(height=400)
            
            text = bar_chart.mark_text(
                align='left',
                baseline='middle',
                dx=5
            ).encode(
                text=alt.Text('vote_count:Q', format=',d')
            )
            
            st.altair_chart(bar_chart + text, use_container_width=True)
        else:
            st.warning("No candidate data available")

    # Historical Trends
    st.markdown("---")
    st.subheader('Voting Trends Over Time')
    
    trends_data = get_historical_trends()
    if not trends_data.empty:
        line_chart = alt.Chart(trends_data).mark_line(point=True).encode(
            x=alt.X('vote_time:T', title='Time'),
            y=alt.Y('total_votes:Q', title='Total Votes'),
            color=alt.Color(
                'party:N',
                scale=alt.Scale(domain=list(PARTY_COLORS.keys()),
                              range=list(PARTY_COLORS.values()))
            ),
            tooltip=['vote_time', 'candidate_name', 'party', 'total_votes']
        ).properties(height=400)
        
        st.altair_chart(line_chart, use_container_width=True)
    else:
        st.warning("No historical trend data available")

    # Continue with the geographical visualization
    st.markdown("---")
    st.subheader('Geographical Vote Distribution')
    
    map_col1, map_col2 = st.columns(2)
    
    with st.spinner("Loading geographical data..."):
        votes_by_state, leading_party = get_geographical_data()
        
        if not votes_by_state.empty and not leading_party.empty:
            try:
                # Load US states geometry
                us_states = gpd.read_file('https://raw.githubusercontent.com/PublicaMundi/MappingAPI/master/data/geojson/us-states.json')
                
                with map_col1:
                    st.write("Total Votes by State")
                    merged_data = us_states.merge(
                        votes_by_state,
                        left_on='name',
                        right_on='address_state',
                        how='left'
                    )
                    
                    if not merged_data.empty:
                        fig1 = px.choropleth(
                            merged_data,
                            geojson=merged_data.geometry,
                            locations=merged_data.index,
                            color='vote_count',
                            color_continuous_scale="Viridis",
                            hover_name="name",
                            hover_data=["parties", "vote_count"]
                        )
                        fig1.update_geos(scope='usa', projection_scale=1.2)
                        fig1.update_layout(margin={"r":0,"t":0,"l":0,"b":0}, height=500)
                        st.plotly_chart(fig1, use_container_width=True)
                
                with map_col2:
                    st.write("Leading Party by State")
                    merged_party_data = us_states.merge(
                        leading_party,
                        left_on='name',
                        right_on='address_state',
                        how='left'
                    )
                    
                    if not merged_party_data.empty:
                        fig2 = px.choropleth(
                            merged_party_data,
                            geojson=merged_party_data.geometry,
                            locations=merged_party_data.index,
                            color='party',
                            color_discrete_map=PARTY_COLORS,
                            hover_name="name",
                            hover_data=["party", "party_votes"]
                        )
                        fig2.update_geos(scope='usa', projection_scale=1.2)
                        fig2.update_layout(margin={"r":0,"t":0,"l":0,"b":0}, height=500)
                        st.plotly_chart(fig2, use_container_width=True)
            except Exception as e:
                st.error(f"Error creating map visualization: {e}")
        else:
            st.warning("No geographical data available")

    # Demographic Analysis
    st.markdown("---")
    st.subheader('Voter Demographics')
    
    demo_col1, demo_col2 = st.columns(2)
    
    with st.spinner("Loading demographic data..."):
        gender_data, age_data = get_demographic_data()
        
        with demo_col1:
            st.write("Gender Distribution")
            if not gender_data.empty:
                fig_gender = px.pie(
                    gender_data,
                    values='percentage',
                    names='gender',
                    hole=0.3,
                    color_discrete_sequence=['#0072C6', '#87CEEB']
                )
                fig_gender.update_traces(textinfo='percent+label')
                fig_gender.update_layout(
                    height=330,
                    margin=dict(t=30, b=0, l=0, r=0),
                    showlegend=True
                )
                st.plotly_chart(fig_gender, use_container_width=True)
            else:
                st.warning("No gender distribution data available")
        
        with demo_col2:
            st.write("Age Distribution")
            if not age_data.empty:
                fig_age = px.bar(
                    age_data,
                    x='age_group',
                    y='count',
                    text='percentage',
                    # Using a purple/violet color that's distinct from the gender chart
                    color_discrete_sequence=['#7B3FF3'] * len(age_data),  # or
                    # Alternative colors you could use:
                    # '#FF6B6B' (coral red)
                    # '#38B6FF' (bright cyan)
                    # '#FFB302' (golden yellow)
                )
                fig_age.update_traces(
                    texttemplate='%{text:.1f}%',
                    textposition='outside'
                )
                fig_age.update_layout(
                    height=330,
                    margin=dict(t=30, b=0, l=0, r=0),
                    yaxis_title="Number of Voters",
                    xaxis_title="Age Group",
                    showlegend=False  # Remove legend since we're using a single color
                )
                st.plotly_chart(fig_age, use_container_width=True)
            else:
                st.warning("No age distribution data available")
    
    # State-level Analysis
    
    st.subheader("State-Level Voting Details")
    state_details = get_state_voting_details()

    # Add search filter for states
    search_state = st.text_input("Search state:", "")
    if search_state:
        state_details = state_details[state_details['State'].str.contains(search_state, case=False)]

    # Function to create alternating backgrounds
    def highlight_rows(row):
        if row.name % 2 == 0:
            return ['background-color: rgba(242, 245, 250, 1)'] * len(row)
        return [''] * len(row)

    # Style the dataframe using Pandas styling
    styled_df = state_details.style\
        .apply(highlight_rows, axis=1)\
        .set_properties(**{
            'padding': '8px 12px',
            'font-size': '14px'
        })\
        .set_table_styles([
            {'selector': 'th', 'props': [
                ('background-color', 'white'),
                ('color', 'rgb(49, 51, 63)'),
                ('font-weight', 'normal'),
                ('border-bottom', '1px solid #ddd'),
                ('padding', '8px 12px'),
                ('font-size', '14px')
            ]},
            {'selector': 'td', 'props': [
                ('border', 'none')
            ]},
            {'selector': 'table', 'props': [
                ('border-collapse', 'collapse'),
                ('border', 'none')
            ]}
        ])\
        .hide(axis="index")

    # Display the styled dataframe
    st.dataframe(
        styled_df,
        column_config={
            "State": st.column_config.TextColumn("State"),
            "Region": st.column_config.TextColumn("Region"),
            "Management Party": st.column_config.NumberColumn("Management Party", format="%d"),
            "Liberation Party": st.column_config.NumberColumn("Liberation Party", format="%d"),
            "United Republic Party": st.column_config.NumberColumn("United Republic Party", format="%d"),
            "Total Votes": st.column_config.NumberColumn("Total Votes", format="%d"),
            "Avg Age": st.column_config.NumberColumn("Average Age", format="%.1f"),
            "Male %": st.column_config.NumberColumn("Male/Female Ratio", format="%.1f%%")
        },
        hide_index=True,
        use_container_width=True
    )

    # Candidate information
    st.subheader('Candidate Information')
    candidate_info = get_candidate_info()
    for _, candidate in candidate_info.iterrows():
        with st.expander(f"{candidate['first_name']} {candidate['last_name']} ({candidate['party']})"):
            col1, col2 = st.columns([1, 3])
            with col1:
                st.image(candidate['img_url'], width=150)
            with col2:
                st.write(f"**Age:** {candidate['age']}")
                st.write(f"**Gender:** {candidate['gender']}")
                st.write(f"**Biography:** {candidate['biography']}")

    # Add footer
    st.markdown("---")
    st.markdown(
        """
        <div style='text-align: center; color: #666;'>
            <p>
                <small>
                    Data updates automatically every 30 seconds. Last update: {}<br>
                    Dashboard refreshed at: {}
                </small>
            </p>
        </div>
        """.format(
            last_update.strftime('%Y-%m-%d %H:%M:%S') if last_update else "N/A",
            datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        ),
        unsafe_allow_html=True
    )

if __name__ == "__main__":
    main()