// Connect to the MongoDB database 'tor_scraper'
const db = connect('mongodb://localhost:27017/tor_scraper');

// Choose the collection to search in (replace 'yourCollection' with the actual collection name)
const collection = db.getCollection('pages');

// Run the query
const results = collection.find({html: {$regex: /colorado/i}});

// Print the results
results.forEach(doc => {
    printjson(doc);
});
