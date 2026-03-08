import random
import time
from datetime import datetime
import requests
import psycopg2
from confluent_kafka import SerializingProducer
import json
import uuid
from datetime import datetime, timedelta

class ContinuousVoteSimulator:
    BASE_URL = 'https://randomuser.me/api/?nat=us'
    PARTIES = ["Management Party", "Liberation Party", "United Republic Party"]

    def __init__(self):
        # Database connection
        self.db_conn = psycopg2.connect(
            host="localhost",
            database="voting",
            user="postgres",
            password="postgres"
        )
        self.db_cur = self.db_conn.cursor()
        
        # Kafka producer
        self.producer = SerializingProducer({
            'bootstrap.servers': 'localhost:9092'
        })
        
        # Initialize candidates if needed
        self.initialize_candidates()
        
        # Cache for candidates
        self.candidates = self.get_all_candidates()
        
        if not self.candidates:
            raise Exception("No candidates available. Please check database initialization.")

    def initialize_candidates(self):
        """Check and initialize candidates if none exist"""
        self.db_cur.execute("SELECT COUNT(*) FROM candidate")
        count = self.db_cur.fetchone()[0]
        
        if count == 0:
            print("No candidates found. Generating candidates...")
            self.generate_initial_candidates()

    def generate_initial_candidates(self, num_candidates=3):
        """Generate initial set of candidates"""
        for i in range(num_candidates):
            try:
                candidate = self.generate_candidate(i)
                self.db_cur.execute("""
                    INSERT INTO candidate (candidate_id, first_name, last_name, dob, age, gender, 
                                      party, biography, img_url)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    candidate['candidate_id'],
                    candidate['first_name'],
                    candidate['last_name'],
                    candidate['dob'],
                    candidate['age'],
                    candidate['gender'],
                    candidate['party'],
                    candidate['biography'],
                    candidate['img_url']
                ))
                self.db_conn.commit()
                print(f"Generated candidate: {candidate['first_name']} {candidate['last_name']} ({candidate['party']})")
            except Exception as e:
                print(f"Error generating candidate {i}: {e}")
                self.db_conn.rollback()

    def generate_candidate(self, candidate_number):
        """Generate a single candidate's data"""
        gender = 'female' if candidate_number % 2 == 1 else 'male'
        response = requests.get(f"{self.BASE_URL}&gender={gender}")
        
        if response.status_code == 200:
            user_data = response.json()['results'][0]
            age = random.randint(32,60)
            return {
                "candidate_id": user_data['login']['uuid'],
                "first_name": user_data['name']['first'],
                "last_name": user_data['name']['last'],
                "dob": (datetime.now() - timedelta(days=age*365)).strftime("%Y-%m-%d"),
                "age": age,
                "gender": gender,
                "party": self.PARTIES[candidate_number % len(self.PARTIES)],
                "biography": (f"{random.choice(['Bachelor', 'Master', 'J.D.', 'Ph.D.'])} degree in {random.choice(['Political Science', 'Law', 'Economics', 'International Relations', 'Public Policy'])} from {random.choice(['Harvard University', 'Stanford University', 'Yale University', 'University of Chicago', 'Princeton University'])}. With {random.randint(7, 20)} years of experience in public service, including roles as {', '.join(random.sample(['State Senator', 'Governor', 'U.S. Representative', 'U.S. Senator', 'Mayor of a major city', 'Secretary of State', 'Vice President'], 2))}. Has demonstrated strong leadership and commitment to national progress. Key achievements include: {', '.join(random.sample(['Passed landmark legislation on healthcare reform', 'Led economic recovery efforts during recession', 'Championed environmental protection initiatives', 'Negotiated crucial international trade agreements', 'Implemented comprehensive education reform', 'Known for bipartisan approach and ability to unite diverse groups.'], 3))}. Committed to economic growth, social justice, and strengthening America's global position."),
                "img_url": user_data['picture']['large']
            }
        else:
            raise Exception("Failed to fetch candidate data")

    def get_all_candidates(self):
        """Fetch all candidates from database"""
        self.db_cur.execute("""
            SELECT candidate_id, first_name, last_name, party 
            FROM candidate
        """)
        return self.db_cur.fetchall()

    def generate_new_voter(self):
        """Generate a new voter"""
        response = requests.get(self.BASE_URL)
        
        if response.status_code == 200:
            user_data = response.json()['results'][0]
            age = random.randint(18,60)
            voter = {
                "voter_id": user_data['login']['uuid'],
                "first_name": user_data['name']['first'],
                "last_name": user_data['name']['last'],
                "dob": (datetime.now() - timedelta(days=age*365)).strftime("%Y-%m-%d"),
                "age": age,
                "gender": user_data['gender'],
                "nationality": user_data['nat'],
                "registration_number": user_data['login']['username'],
                "address_street": f"{user_data['location']['street']['number']} {user_data['location']['street']['name']}",
                "address_city": user_data['location']['city'],
                "address_state": user_data['location']['state'],
                "address_country": user_data['location']['country'],
                "address_postcode": str(user_data['location']['postcode']),
                "email": user_data['email'],
                "phone": user_data['phone']
            }
            return voter
        else:
            raise Exception("Failed to fetch voter data")

    def register_voter(self, voter):
        """Register a new voter in the system"""
        try:
            # Insert into database
            self.db_cur.execute("""
                INSERT INTO voter (voter_id, first_name, last_name, dob, age, gender, 
                               nationality, registration_number, address_street, 
                               address_city, address_state, address_country, 
                               address_postcode, email, phone)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                voter['voter_id'], voter['first_name'], voter['last_name'], voter['dob'], voter['age'],
                voter['gender'], voter['nationality'], voter['registration_number'],
                voter['address_street'], voter['address_city'], voter['address_state'],
                voter['address_country'], voter['address_postcode'], voter['email'], voter['phone']
            ))
            self.db_conn.commit()

            # Send to Kafka
            self.producer.produce(
                'voters_topic',
                key=voter['voter_id'],
                value=json.dumps(voter),
                on_delivery=self.kafka_delivery_report
            )
            self.producer.flush()
            return True

        except Exception as e:
            print(f"Error registering voter: {e}")
            self.db_conn.rollback()
            return False

    def get_weighted_candidate(self):
    #Get a candidate based on predetermined voting percentages
    # Define weights for each candidate (22%, 40%, 35%)
        weights = [0.22, 0.40, 0.35]  # Note: Intentionally sums to 97% to account for some margin
        
        # Generate a random number between 0 and 1
        r = random.random()
        
        # Calculate cumulative probabilities
        cumulative_prob = 0
        for i, weight in enumerate(weights):
            cumulative_prob += weight
            if r <= cumulative_prob:
                return self.candidates[i]
        
        # If we somehow get here, return the last candidate
        return self.candidates[-1]

    def generate_vote(self, voter):
        """Generate a vote for a given voter using weighted probabilities"""
        candidate = self.get_weighted_candidate()
        vote_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        
        return {
            "vote_id": str(uuid.uuid4()),
            "voter_id": voter['voter_id'],
            "voter_name": f"{voter['first_name']} {voter['last_name']}",
            "candidate_id": candidate[0],
            "candidate_name": f"{candidate[1]} {candidate[2]}",
            "party": candidate[3],
            "voted_at": vote_time,
            "vote": 1
        }

    def get_current_stats(self):
        """Get current voting statistics with percentages"""
        self.db_cur.execute("SELECT COUNT(*) FROM voter")
        total_voters = self.db_cur.fetchone()[0]

        self.db_cur.execute("""
            SELECT 
                c.first_name, 
                c.last_name, 
                c.party, 
                COUNT(*) as vote_count,
                ROUND(COUNT(*) * 100.0 / NULLIF(SUM(COUNT(*)) OVER (), 0), 2) as percentage
            FROM candidate c
            LEFT JOIN vote v ON c.candidate_id = v.candidate_id
            GROUP BY c.candidate_id, c.first_name, c.last_name, c.party
            ORDER BY vote_count DESC
        """)
        results = self.db_cur.fetchall()
        
        print("\nCurrent Statistics:")
        print("-" * 60)
        print(f"Total Registered Voters: {total_voters}")
        print("\nVotes by Candidate:")
        total_votes = sum(result[3] for result in results)
        for result in results:
            votes = result[3]
            percentage = result[4] if result[4] is not None else 0.0
            print(f"{result[0]} {result[1]} ({result[2]}): {votes} votes ({percentage}%)")
        print("-" * 60)

    def cast_vote(self, vote_data):
        """Record vote in database and send to Kafka"""
        try:
            # Insert into database
            self.db_cur.execute("""
                INSERT INTO vote (vote_id, voter_id, candidate_id, voted_at, vote)
                VALUES (%s, %s, %s, %s, %s)
            """, (
                vote_data['vote_id'],
                vote_data['voter_id'],
                vote_data['candidate_id'],
                vote_data['voted_at'],
                vote_data['vote']
            ))
            self.db_conn.commit()

            # Send to Kafka
            self.producer.produce(
                'votes_topic',
                key=vote_data['vote_id'],  # Changed to use vote_id as key
                value=json.dumps(vote_data),
                on_delivery=self.kafka_delivery_report
            )
            self.producer.flush()
            return True

        except Exception as e:
            print(f"Error casting vote: {e}")
            self.db_conn.rollback()
            return False

    def kafka_delivery_report(self, err, msg):
        """Callback for Kafka producer to report delivery results"""
        if err is not None:
            print(f'Message delivery failed: {err}')

    

    def simulate_continuously(self, voter_interval=0.01, stats_interval=10):
        """Run continuous simulation of voter registration and voting"""
        print("Starting continuous simulation...")
        print(f"- Generating new voter every {voter_interval} seconds")
        print(f"- Showing stats every {stats_interval} seconds")
        
        # Show initial candidates
        print("\nCurrent Candidates:")
        for candidate in self.candidates:
            print(f"- {candidate[1]} {candidate[2]} ({candidate[3]})")
        print()
        
        last_stats_time = time.time()
        votes_generated = 0
        
        try:
            while True:
                # Generate and register new voter
                try:
                    new_voter = self.generate_new_voter()
                    if self.register_voter(new_voter):
                        # Generate and cast vote for new voter
                        vote_data = self.generate_vote(new_voter)
                        if self.cast_vote(vote_data):
                            votes_generated += 1
                            print(f"Vote cast: {vote_data['voter_name']} voted for {vote_data['candidate_name']} ({vote_data['party']})")
                
                except Exception as e:
                    print(f"Error in simulation cycle: {e}")

                # Show stats periodically
                current_time = time.time()
                if current_time - last_stats_time >= stats_interval:
                    self.get_current_stats()
                    last_stats_time = current_time
                    
                time.sleep(voter_interval)

        except KeyboardInterrupt:
            print("\nSimulation stopped by user")
            print(f"Total votes generated: {votes_generated}")
        
        finally:
            self.cleanup()

    def cleanup(self):
        """Clean up database and Kafka connections"""
        self.db_cur.close()
        self.db_conn.close()

if __name__ == "__main__":
    simulator = ContinuousVoteSimulator()
    
    try:
        # Start continuous simulation
        simulator.simulate_continuously(voter_interval=0.01, stats_interval=10)
        
    except Exception as e:
        print(f"Error during simulation: {e}")