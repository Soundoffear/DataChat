from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, g
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, send, emit
import os
from datetime import datetime
import openai
import pandas as pd
import uuid
import json
import sqlite3

from chat_bot import ChatBot

openai.api_key = "<YOUR OPENAI API KEY>"

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{os.path.join(app.instance_path, 'chat.db')}"
app.config["SQLALCHEMY_BINDS"] = {"data_mud": f"sqlite:///{os.path.join(app.instance_path, '<YOUR DATABASE SQLITE3>')}"}
db = SQLAlchemy(app)

socketio = SocketIO(app)

chat_instance = ChatBot("You are helpful Assistant")

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text)
    person = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    type_of_message = db.Column(db.Text)
    svg_name = db.Column(db.Text)
    query_sql = db.Column(db.Text)
    plot_type = db.Column(db.Text)
    text_analysis = db.Column(db.Text)

@app.route('/')
def index():
    return render_template('index.html')

def get_table_names(conn):
    """Return a list of table names"""
    table_names = []
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table';")
    for table in tables.fetchall():
        table_names.append(table[0])
        
    return table_names

def get_column_names(conn, table_name):
    column_names = []
    columns = conn.execute(f"PRAGMA table_info({table_name});").fetchall()
    for col in columns:
        column_names.append(col[1])
        
    return column_names

def get_database_info(conn):
    table_dicts = []
    for table_name in get_table_names(conn):
        columns_names = get_column_names(conn, table_name)
        table_dicts.append({"table_name": table_name, "column_names": columns_names})
        
    return table_dicts

def plot_data(data_query, function_type="code", plot_type="line", xaxis="time", yaxis="temperature"):
    """Create a line chart with D3.js"""
    
    plot_info = {
        "function_type": function_type,
        "plot_type": plot_type,
        "xaxis": xaxis,
        "yaxis": yaxis,
        "data_query": data_query,
    }
    
    return json.dumps(plot_info)

def normal_question(answer, function_type="text"):
    
    answer_dict = {
        "answer": answer,
        "function_type": function_type,
    }
    
    return json.dumps(answer_dict)

def db_questions(conn, query):
    print(query)
    try:
        results = str(conn.execute(query).fetchall())
    except Exception as e:
        print(e)
    
    return results

def data_analysis(conn, query):
    
    try:
        results = str(conn.execute(query).fetchall())
    except Exception as e:
        print(e)
    
    return results

def execute_fuction_call(message):
    results = None
    query = None
    function_type = None
    try:
        if message["function_call"]["name"] == "db_questions":
            with db.engines["data_mud"].begin() as conn:
                query = json.loads(message["function_call"]["arguments"])["query"]
                function_type = json.loads(message["function_call"]["arguments"])["function_type"]
                results = db_questions(conn, query)
        elif message["function_call"]["name"] == "data_analysis":
            print(message)
            with db.engines["data_mud"].begin() as conn:
                query = json.loads(message["function_call"]["arguments"])["data_query"]
                function_type = json.loads(message["function_call"]["arguments"])["function_type"]
                results = data_analysis(conn, query)
        else:
            results = "Function not found"
    except Exception as e:
        print(f"Error: {e}")
        
    finally:
            
        return results, query, function_type

@socketio.on('user_input')
def msg_received(msg):
    print(msg)
    message = Message(content=msg['content'], timestamp=datetime.utcnow(), person='User', type_of_message="normal", svg_name="N/A", query_sql="N/A", plot_type="N/A")
    db.session.add(message)
    db.session.commit()
    
    functions = [{
            "name": "plot_data",
            "description": """Only when asked for plot. Use SQL query to generate proper data for chart in D3.js. This function will return a JSON object with the following keys: type, xaxis, yaxis. QUERY NEEDS TO CONTAIN Well_ID.
                            ALWAY REMEMBER TO INCLUDE WELL_ID IN THE QUERY EG. SELECT Well_ID, ... FROM <table_name>;""",
            "parameters": {
                "type": "object",
                "properties": {
                    "function_type": {"type": "string", "enum": ["code"]},
                    "plot_type": {"type": "string", "enum": ["line", "bar"]},
                    "xaxis": {"type": "string", "enum": ["FullTime"]},
                    "yaxis": {"type": "string",
                              "description": "Column name of the data to be plotted based on SQL query."
                              },
                    "data_query": {
                        "type": "string",
                        "description": f"""
                            SQL query extracting info to answer the user's question.
                            SQL should be written using this database schema:
                            {database_schema_string}
                            If asked about multiple wells the query should be written to include all wells e.g. SELECT Well_ID, .... FROM <table_name>
                            You are to operate in chain of thought:
                            Question Evaluation => THOUGHT => MAKE DECISION => ACTION => OBSERVATION => REITERATE IF NEEDED => OUTPUT
                            For example:
                            Question: What is revenue from the well?
                            Thought: I need to get revenue from the database. Which table is it in? What is the column name? Do I need to calculate it?
                            Decision: Accessing table with products and products prices. If revenue is not there, I will calculate it.
                            Action: SQL query to get revenue from the database. Select appropriate plot from list: bar, line
                            Reiterate if needed: If the query is not correct, I will try to fix it.
                            Output: The revenue from the well is...
                            SOME BASE CALCULATION:
                            REVENUE = Quantity * price;
                            PROFIT per product = Quantity * price - Quantity * cogs;
                            Cost per foot OR Price per foot = (Quantity * price) / max(depth);
                            Ensure proper xaxis and yaxis names are used based on any created SQL query.
                            ALWAY REMEMBER TO INCLUDE WELL_ID IN THE QUERY EG. SELECT Well_ID, ... FROM <table_name>;
                            Well_ID is the unique identifier for each well. Is always integer.
                        """
                    }
                },
                "required": ["function_type", "plot_type", "xaxis", "yaxis", "data_query"],
            },
        },
        {
            "name": "data_analysis",
            "description": "Use this function when asked to provide data analysis. Use SQL query to generate proper data for analysis when applicable.",
            "parameters": {
                "type": "object",
                "properties": {
                    "data_query": {
                        "type":"string",
                        "description": f"""Answer to the question asked by the user. This answer should be in plain text. Your data analysis should be included in the answer.
                            If data is not provided to you in the question, you should use SQL query to generate proper data for analysis.
                            Your database information: {database_schema_string}.
                            YP in data base is yield point
                            PV in data base is plastic viscosity
                            MAKE SURE TO WRITE SQL QUERY TO INCLUDE ALL WELLS IF ASKED ABOUT MULTIPLE WELLS.
                        """
                    },
                    "data_analysis": {
                        "type": "string",
                        "description": f"""if data is provided to you in the question, you should use this data to perform analysis.
                                        Answer to the question asked by the user. This answer should be in plain text. Your data analysis should be included in the answer.
                                        It should be paragrpah of text, not a single sentence or list of values.
                                        NO PYTHON CODE ALLOWED. THIS NEEDS TO BE REPORT TEXT."""
                    },
                    "function_type": {
                        "type": "string",
                        "enum": ["analysis"],
                    }
                },
                "required": ["data_query", "function_type", "data_analysis"],
            }
        },
        {
            "name": "db_questions",
            "description": """Use this function to answer questions about product usage or mud properties.
                            For example: 
                            1. How much product was used? => You should create query that would return product name and quantity used.
                            2. What was the difference in Product A usage between Well 1 and Well 2? => You should create query that would return product name and quantity used for both wells.
                            """,
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": f"""
                            SQL query extracting info to answer the user's question.                            
                            
                            SQL should be written using this database schema:
                            {database_schema_string}
                            The query should be returned in plain text, not in JSON format.
                            YP in data base is yield point
                            PV in data base is plastic viscosity
                            You are to operate in chain of thought:
                            Question Evaluation => THOUGHT => MAKE DECISION => ACTION => OBSERVATION => REITERATE IF NEEDED => OUTPUT
                            For example:
                            Question: What is revenue from the well?
                            Thought: I need to get revenue from the database. Which table is it in? What is the column name? Do I need to calculate it?
                            Decision: Accessing table with products and products prices. If revenue is not there, I will calculate it.
                            Action: SQL query to get revenue from the database.
                            Reiterate if needed: If the query is not correct, I will try to fix it.
                            Output: The revenue from the well is...

                            ### If asked about multiple wells the query should be written to include all wells e.g. SELECT Well_ID, .... FROM <table_name>
                            
                            SOME BASE CALCULATION:
                            REVENUE = Quantity * price;
                            PROFIT per product = Quantity * price - Quantity * cogs;
                            Cost per foot OR Price per foot = (Quantity * price) / max(depth);
                            ALWAY REMEMBER TO INCLUDE WELL_ID IN THE QUERY EG. SELECT Well_ID, ... FROM <table_name>;
                        """,
                    },
                    "section": {
                        "type": "string",
                        "enum": ["16", "12.25", "8.5"],
                    },
                    "function_type": {
                        "type": "string",
                        "enum": ["table"],
                    }
                },
                "required": ["query", "function_type"],
            }
        },
        {
            "name": "normal_question",
            "description": """You are and Oil & Gas assistant. Your job is to provide answers only about oil & gas and only to oil & gas question. All other questions you are only allowed to say 'I am Oil & Gas assistant, can't answer this question'.

                                Your chain-of-thought operation looks like this:

                                Question Evaluation => Decision "Oil & Gas Question" or "Not Oil & Gas Question":
                                => IF "Oil & Gas Question" you answer the question
                                => IF "Not Oil & Gas Question" =>  'I am Oil & Gas assistant, can't answer this question'.""",
            "parameters": {
                "type": "object",
                "properties": {
                    "answer": {
                        "type": "string",
                        "description": """You are and Oil & Gas assistant. Your job is to provide answers only about oil & gas and only to oil & gas question. All other questions you are only allowed to say 'I am Oil & Gas assistant, can't answer this question'.

                                        Your chain-of-thought operation looks like this:

                                        Question Evaluation => Decision "Oil & Gas Question" or "Not Oil & Gas Question":
                                        => IF "Oil & Gas Question" you answer the question
                                        => IF "Not Oil & Gas Question" =>  'I am Oil & Gas assistant, can't answer this question'.
                        """,
                    },
                    "function_type": {
                        "type": "string",
                        "enum": ["code", "text", "table"],
                    }
                },
                "required": ["function_type", "answer"],
            }
        }
    ]
    
    response = chat_instance(msg['content'], functions=functions)
    
    response_message = response["choices"][0]["message"]
    
    print("FIRST RESPONSE", response_message)
    function_args = {"function_type": "text"}
    
    second_response = None
    
    if response_message.get("function_call"):
        available_functions = {
            "plot_data": plot_data,
            "db_questions": db_questions,
            "normal_question": normal_question,
            "data_analysis": data_analysis,
        }
        
        function_name = response_message["function_call"]["name"]
        if function_name == "plot_data":
            print("PLOT DATA")
            function_to_call = available_functions[function_name]
            json_str = response_message['function_call']['arguments']
            
            function_args = json.loads(json_str)
            function_response = function_to_call(
                function_type=function_args.get("function_type"),
                plot_type=function_args.get("type"),
                xaxis=function_args.get("xaxis"),
                yaxis=function_args.get("yaxis"),
                data_query=function_args.get("data_query"),
            )
            
        elif function_name == "db_questions":
            print("DB QUESTIONS")
            results = execute_fuction_call(response_message)
            
            if not results[2]:
                function_args = {
                    "query": results[1],
                    "function_type": "table"
                }
            else:
                function_args = {
                    "query": results[1],
                    "function_type": results[2]
                    }
            
            function_response = results[0]
            
        elif function_name == "normal_question":
            function_to_call = available_functions[function_name]
            json_str = response_message['function_call']['arguments']
            
            function_args = json.loads(json_str)
            
            function_response = function_to_call(
                answer=function_args["answer"],
                function_type=function_args["function_type"],   
            )
            
        elif function_name == "data_analysis":
            print("DATA ANALYSIS")
            results = execute_fuction_call(response_message)
            print("TEST RESULTS", results)
            function_args = {
                "query": results[1],
                "function_type": results[2]
                }
            
            function_response = results[0]
        
        print("Function Response", function_response)
        print("Function Name", function_name)
        if function_name == "table" or function_name == "db_questions":
            print("TABLE", function_response)
            
            second_response = ""
            
        elif function_name == "plot_data":
            second_response = json.loads(function_response)["plot_type"]
            print(second_response)
        elif function_name == "data_analysis":
            print("ANALYSIS", response_message["content"])
            second_response = chat_instance(f"Perform data analysis of provided data: {function_response}", functions=functions)
        else:
            print("NORMAL QUESTION", response_message["content"])
            second_response = json.loads(function_response)["answer"]
    else:
        second_response = response_message["content"] 
    
    print("SECOND RESPONSE", second_response)
    print("FUNCTION ARGS", function_args)
    
    if function_args["function_type"] == 'code':
        
        svg_uuid = "id"+str(uuid.uuid4())
        xaxis = function_args.get("xaxis")
        yaxis = function_args.get("yaxis")
        
        
        with db.engines["data_mud"].begin() as conn:
            print(function_args)
            if "data_query" in function_args.keys():
                df_data = pd.read_sql_query(function_args["data_query"], conn)
                db.session.add(Message(content=xaxis + " | " + yaxis, timestamp=datetime.utcnow(), person='AI', type_of_message="code", svg_name=svg_uuid, query_sql=function_args["data_query"], plot_type=function_args["plot_type"]))
                db.session.commit()
            else:
                df_data = pd.read_sql_query(function_args["query"], conn)
                db.session.add(Message(content=xaxis + " | " + yaxis, timestamp=datetime.utcnow(), person='AI', type_of_message="code", svg_name=svg_uuid, query_sql=function_args["query"], plot_type=function_args["plot_type"]))
                db.session.commit()
        
        emit('response', {'content': [xaxis, yaxis], 
                          "person": "AI", 
                          "type":"code", 
                          "svg_name":svg_uuid, 
                          "data_plot":json.dumps(df_data.to_dict("records")),
                          "plot_type":function_args["plot_type"]}, 
             broadcast=True)
        
    elif function_args["function_type"] == 'table':
        query = function_args["query"]
        with db.engines["data_mud"].begin() as conn:
            dft = pd.read_sql_query(query, conn)
        db.session.add(Message(content=query, timestamp=datetime.utcnow(), person='AI', type_of_message="table", svg_name="N/A", query_sql="N/A", plot_type="N/A", text_analysis=second_response))
        db.session.commit()
        
        emit('response', {'content': dft.to_html(), "person": "AI", "type":"table", "comment":second_response}, broadcast=True)
    elif function_args["function_type"] == 'analysis':
        print(function_args)
        snd_response = json.loads(second_response["choices"][0]["message"]["function_call"]["arguments"])
        print(snd_response["data_analysis"])
        db.session.add(Message(content=snd_response["data_analysis"], timestamp=datetime.utcnow(), person='AI', type_of_message="analysis", svg_name="N/A", query_sql="N/A", plot_type="N/A", text_analysis=snd_response["data_analysis"]))
        db.session.commit()
        
        emit('response', {'content': snd_response["data_analysis"], "person": "AI", "type":"analysis"}, broadcast=True)
    else:
        if type(second_response) != str and second_response.get("choices") is not None and len(second_response.get("choices")) > 0:
            second_response = second_response["choices"][0]["message"]["content"]
        db.session.add(Message(content=second_response, timestamp=datetime.utcnow(), person='AI', type_of_message="regular", svg_name="N/A", query_sql="N/A", plot_type="N/A"))
        db.session.commit()
        emit('response', {'content': second_response, "person": "AI", "type":"regular"}, broadcast=True)

@app.route('/messages')
def get_messages():
    messages = Message.query.all()
    
    messages_content = [message.content for message in messages]
    messages_person = [message.person for message in messages]
    messages_type = [message.type_of_message for message in messages]
    messages_svg = [message.svg_name for message in messages]
    messages_sql = [message.query_sql for message in messages]
    messages_plot_type = [message.plot_type for message in messages]
    messages_analysis = [message.text_analysis for message in messages]
    print(messages_analysis)
    with db.engines["data_mud"].begin() as conn:
        messages_content = [pd.read_sql_query(message, conn).to_html() if type == "table" else message for message, type in zip(messages_content, messages_type)]
        messages_content = [msg.split(" | ") if type == "code" else msg for msg, type in zip(messages_content, messages_type)]
        messages_sql = [json.dumps(pd.read_sql_query(message, conn).to_dict("records")) if type == "code" else message for message, type in zip(messages_sql, messages_type)]
        
    return jsonify({'content': messages_content, 'person': messages_person, 'type': messages_type, 'svg': messages_svg, "data_sql": messages_sql, "plot_type": messages_plot_type, "text_analysis": messages_analysis})
if __name__ == '__main__':
    with app.app_context():
        database_schema_dict = get_database_info(db.engines["data_mud"])
        database_schema_string = "\n".join([
            f"Table: {table['table_name']}\nColumns: {table['column_names']}\n" for table in database_schema_dict
        ])
        
    socketio.run(app, debug=True, host="192.168.1.3", port="5000")
