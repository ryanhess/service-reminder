from mysql.connector import connect, Error

connection = connect(
    host = "localhost",
    user = "serv-rem-dev",
    password = "password",
    database = "service_reminders_app"
)
c1 = connection.cursor()
c1.execute("SHOW DATABASES")