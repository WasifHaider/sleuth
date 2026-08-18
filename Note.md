SLEUTH

**Config.py:**

The [config.py](http://config.py) is a overall setting of the class that consists of a immutable frozen class that loads all the secrets from the dotenv using load_dotenv and then here we check in load_config() if three required keys are present or not voyage api key, groq api key and database url. Groq model is NOT required - it is optional and defaults to `llama-3.3-70b-versatile` if not set. Then it downstreams - means all the modules requiring these keys will recieve a Config object which they cannot change just use it in this way they cannot reach the actual .env but a config object only.

**schema.sql/db.py:**

`schema.sql` defines the database structure for the code indexing system. It creates a `repos` table to store information about each indexed GitHub repository, including its indexing status, any errors, and the last indexing time. It also creates a `chunks` table that stores individual code chunks (such as functions, methods, or classes), along with their **1024-dimensional embedding vectors** for semantic search and a **content hash** to detect whether the code has changed since the last indexing. A unique index on `(repo_id, file_path, symbol_name)` ensures that re-indexing updates existing records instead of creating duplicates, making incremental indexing efficient. The `sleuth/db.py` file provides helper functions to connect to the PostgreSQL database using **Psycopg**, registers the **pgvector** adapter so Python can store and retrieve embedding vectors, and includes an `apply_schema()` function that executes `schema.sql`. Since the SQL uses `CREATE ... IF NOT EXISTS`, the schema can be safely applied multiple times without causing errors, allowing the application to automatically initialize the database whenever it starts. We have aslo created one more index for HNSW ( Hierarchichal Navigable Small World) - so HNSW is a whole concept of searching through the vectors. First the problem is with simple searching of vectors is that we have find the nearest neighbour of the vector with every other vector it may work on small datasets but it will fail eventually on large ones - so we used graph implementing the concept of six degrees of seperation that is basically the NSW the navigable small world in which every vector is created as a node connected with other vectors but if we simply use that it is also a problem that we will look almost forever in the graph if the dataset is big that why NSW provided a technique - creating large connection among the vectors e.g I want to go to house X, my friend is in a area of that house so I would hop in that house then find the others so my searching becomes efficient but NSW also contains a problem that it only see's the local area - it will look for a vector neighbour check all and will reach a endpoint but there may be a case that there was a better region with more best candidates - for that even if we create long edge connections with every node it will take alot of memory and will become even complex that is why we used HNSW ( I didn't know this ) HNSW provides us layers of graph on top of the others with the nodes and neighbours the lowest layer has all the vector nodes connected and the upper lever from it has comparitively less nodes then that and the number goes even small exponentionaly if we move to the top most layer, now the searching starting point starts from the top layers and it moves down unconditionaly after finding the best there - this concept I got wrong - and in this way we can find the neighbours fast and accurate. Then looking at our index of chunks_embedding_idx we can also use some params such as "m" and "ef_construction". "m" is used for specifying how many edges will be created for each node greater the nodes means greater the oppurtunity to find the best neighbours and on the other hand "ef_construction" is the number of list of candidates a vector, a node will be compared at insertion point the greater the number becomes the greater the chance of getting the best matching comes. Ususally the ef_construction should be greater or equal to the "m". Then we used operation of cosine to find the similarity instead of others because most the embedding models use the angle between the vectors - meaning in which direction the vector points to find the similarity between the vector.

**chunking.py:**

Okay so in this file we have basically created a class that takes code snipper and generates useful context for the embedding model, we are giving extra information so that the embedding model generates an overall accurate embedding and we will use the same function when giving to LLM and we have kept it centralized because while input and output we tend to keep the format consistent so that our model does not get confused. We are using SHA-256 for creating a hash for the code lines so afterwards if we want to check if the code is changed the hash will tell us.

**parse.py:**

So this is the entry point for the tree-sitter. We have created basically a class of language spec that contains a key and the ts_language (tree-sitter language). Then we have a map for intializing the class of language spec with a instance of the relevant language by their extension e.g **.py** or **.ts**. We have also created a class for unsupported file so that our code, our pipeline does not crashes and returns silently and everything is kept centralized so if we need to add more languages we would simply import it and add a map entry for that language. Then we have a function of parsing the code chunk. We are simply getting the source bytes and extension. From extension we can simply get what type of language it is and then call the Parser for that specific language first from the tree-sitter and then using that parser parsing the source bytes - it returns the Tree (AST) we needed for our chunking along with the spec that from which language does it belong. We will use all of this in chunking too by importing the funcitons we need in chunk.py that we will dicuss.

**chunk.py:**

This is where we actually chunk the tree we get from the parser. We check whether the
parser spec is python or js, but use a single generic walker (`_walk_generic`) for both,
since they have the same shape — top-level functions, top-level classes, methods inside
classes. Only the node type _names_ differ under the hood (e.g. `function_definition` vs
`function_declaration`), so those are passed in explicitly per language.

The walker looks at direct children of the root and buckets them:

- matches function type -> function chunk
- matches class type -> if it has methods, one chunk per method; else one chunk for
  the whole class
- anything else -> collected into a single "junk"/module chunk at the end

**Known issues to fix:**

1. Only exact node types at the top level are matched. If a function/class is wrapped —
   e.g. `export class Foo {}` in TS/JS, or a decorator like `@app.get(...)` in Python —
   the wrapper node (`export_statement` / `decorated_definition`) doesn't match
   `function_type`/`class_type`, even though there's a real function/class underneath.
   The whole thing falls into junk instead of being chunked properly. This affects
   basically all of our NestJS/Vue exports and FastAPI route handlers.
2. Worse: if a class mixes plain and decorated methods (e.g. a `@property` method next
   to a normal one), the decorated method doesn't match `method_type` — and since only
   root children go into the junk bucket, it doesn't land there either. It's silently
   dropped, not chunked anywhere.
3. The junk/module chunk's reported start_line/end_line span the full range from first
   to last leftover node, even though the actual text skips everything that got pulled
   into other chunks — so the line numbers don't match the content.

Fix: unwrap `decorated_definition`/`export_statement` before checking node type, but
still use the outer node's byte span so the decorator/export keyword stays in the chunk text.
