import psycopg2
from psycopg2 import Error
import time

class DatabaseSetup:
    def __init__(self, host="localhost", user="postgres", password="postgres"):
        self.connection_params = {
            "host": host,
            "user": user,
            "password": password
        }
        self.database = "voting"

    def create_database(self):
        """Create the database if it doesn't exist"""
        conn_params = self.connection_params.copy()
        conn_params["database"] = "postgres"
        
        try:
            conn = psycopg2.connect(**conn_params)
            conn.autocommit = True
            cur = conn.cursor()
            
            # Check if database exists
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (self.database,))
            exists = cur.fetchone()
            
            if not exists:
                print(f"Creating database {self.database}...")
                cur.execute(f"CREATE DATABASE {self.database}")
                print(f"Database {self.database} created successfully!")
            else:
                print(f"Database {self.database} already exists.")
                
            cur.close()
            conn.close()
            
        except Error as e:
            print(f"Error creating database: {e}")
            raise e

    def connect_to_db(self):
        """Connect to the voting database"""
        try:
            conn_params = self.connection_params.copy()
            conn_params["database"] = self.database
            return psycopg2.connect(**conn_params)
        except Error as e:
            print(f"Error connecting to PostgreSQL: {e}")
            raise e

    def create_tables(self):
        """Create all necessary tables for the voting system"""
        commands = [
            """
            DROP TABLE IF EXISTS vote;
            """,
            """
            DROP TABLE IF EXISTS voter;
            """,
            """
            DROP TABLE IF EXISTS candidate;
            """,
            """
            CREATE TABLE candidate (
                candidate_id VARCHAR(255) PRIMARY KEY,
                first_name VARCHAR(255) NOT NULL,
                last_name VARCHAR(255) NOT NULL,
                dob VARCHAR(225) NOT NULL,
                age INTEGER,
                gender VARCHAR(10) NOT NULL,
                party VARCHAR(255) NOT NULL,
                biography TEXT,
                img_url TEXT
            )
            """,
            """
            CREATE TABLE voter (
                voter_id VARCHAR(255) PRIMARY KEY,
                first_name VARCHAR(255) NOT NULL,
                last_name VARCHAR(255) NOT NULL,
                dob VARCHAR(225) NOT NULL,
                age INTEGER,
                gender VARCHAR(10) NOT NULL,
                nationality VARCHAR(100),
                registration_number VARCHAR(255) UNIQUE,
                address_street VARCHAR(255),
                address_city VARCHAR(255),
                address_state VARCHAR(255),
                address_country VARCHAR(255),
                address_postcode VARCHAR(255),
                email VARCHAR(255),
                phone VARCHAR(100)
            )
            """,
            """
            CREATE TABLE vote (
                vote_id VARCHAR(255) PRIMARY KEY,
                voter_id VARCHAR(255) NOT NULL,
                candidate_id VARCHAR(255) NOT NULL,
                voted_at TIMESTAMP NOT NULL,
                vote INTEGER,
                FOREIGN KEY (voter_id) REFERENCES voter(voter_id),
                FOREIGN KEY (candidate_id) REFERENCES candidate(candidate_id),
                CONSTRAINT unique_voter UNIQUE (voter_id)
            )
            """,
            """
            CREATE INDEX idx_vote_candidate_id ON vote(candidate_id);
            """,
            """
            CREATE INDEX idx_vote_voted_at ON vote(voted_at);
            """,
            """
            CREATE INDEX idx_voter_state ON voter(address_state);
            """
        ]

        conn = None
        try:
            conn = self.connect_to_db()
            cur = conn.cursor()
            
            # Execute each command
            for command in commands:
                cur.execute(command)
            
            # Commit the changes
            conn.commit()
            
            # Verify tables were created
            cur.execute("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public'
            """)
            tables = cur.fetchall()
            print("\nCreated tables:")
            for table in tables:
                print(f"- {table[0]}")
            
            cur.close()
            print("\nDatabase setup completed successfully!")
            
        except (Exception, Error) as e:
            print(f"\nError: {e}")
            if conn:
                conn.rollback()
        finally:
            if conn:
                conn.close()

    def verify_setup(self):
        """Verify that all tables were created correctly"""
        try:
            conn = self.connect_to_db()
            cur = conn.cursor()

            # Check each table structure
            tables = ['candidate', 'voter', 'vote']
            for table in tables:
                print(f"\nStructure for table '{table}':")
                cur.execute(f"""
                    SELECT column_name, data_type, character_maximum_length
                    FROM information_schema.columns
                    WHERE table_name = '{table}'
                """)
                columns = cur.fetchall()
                for col in columns:
                    print(f"- {col[0]}: {col[1]}", end="")
                    if col[2]:
                        print(f" (max length: {col[2]})")
                    else:
                        print()

            cur.close()
            conn.close()
            print("\nVerification completed!")
            
        except (Exception, Error) as e:
            print(f"Error during verification: {e}")

if __name__ == "__main__":
    print("Starting database setup...")
    print("Waiting for PostgreSQL to be ready...")
    time.sleep(5)  # Give PostgreSQL container time to fully start
    
    # Create and setup database
    db_setup = DatabaseSetup()
    
    try:
        # First create the database
        db_setup.create_database()
        
        # Then create tables
        db_setup.create_tables()
        
        # Verify the setup
        print("\nVerifying database setup...")
        db_setup.verify_setup()
        
    except Exception as e:
        print(f"Setup failed: {e}")