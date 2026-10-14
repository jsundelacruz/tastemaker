-- Visual Research Archive - Supabase/Postgres Schema
-- Run this in Supabase SQL Editor to set up your database

-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Images table: stores moodboard images with embeddings
CREATE TABLE IF NOT EXISTS images (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_platform TEXT NOT NULL CHECK (source_platform IN ('pinterest', 'cosmos', 'instagram', 'figma', 'manual')),
    source_collection TEXT,
    image_url TEXT NOT NULL,
    local_path TEXT,
    original_title TEXT,
    original_description TEXT,
    original_metadata JSONB DEFAULT '{}',
    user_note TEXT,
    user_tags TEXT[] DEFAULT '{}',
    -- Agent-generated fields
    visual_summary TEXT,
    detected_materials TEXT[] DEFAULT '{}',
    detected_mood TEXT,
    color_palette JSONB DEFAULT '[]',
    -- Vector embedding (CLIP ViT-B/32 produces 512 dimensions)
    embedding vector(512),
    -- Timestamps
    date_saved TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Collections table: aggregates board metadata and agent analysis
CREATE TABLE IF NOT EXISTS collections (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    collection_name TEXT NOT NULL,
    source_platform TEXT NOT NULL,
    total_images INTEGER DEFAULT 0,
    -- Agent-generated analysis
    material_vocabulary JSONB DEFAULT '[]',
    spatial_qualities JSONB DEFAULT '[]',
    recurring_forms JSONB DEFAULT '[]',
    color_palette JSONB DEFAULT '[]',
    mood_atmosphere TEXT,
    installation_directions JSONB DEFAULT '[]',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Unique constraint for upsert by collection name + platform
DROP INDEX IF EXISTS collections_name_platform_uniq;
ALTER TABLE collections DROP CONSTRAINT IF EXISTS collections_name_platform_uniq;
ALTER TABLE collections ADD CONSTRAINT collections_name_platform_uniq
UNIQUE (collection_name, source_platform);

-- User taste: one row, overall synthesis of all collections (Option B: stored, re-analyze on demand)
CREATE TABLE IF NOT EXISTS user_taste (
    id TEXT PRIMARY KEY DEFAULT 'default',
    content TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
-- App upserts the single row (id='default') when saving taste.

-- Index for vector similarity search (cosine distance)
CREATE INDEX IF NOT EXISTS images_embedding_idx ON images 
USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- Index for filtering by source
CREATE INDEX IF NOT EXISTS images_source_platform_idx ON images (source_platform);
CREATE INDEX IF NOT EXISTS images_source_collection_idx ON images (source_collection);
CREATE INDEX IF NOT EXISTS images_created_at_idx ON images (created_at DESC);

-- Trigger to auto-update updated_at
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS update_images_updated_at ON images;
CREATE TRIGGER update_images_updated_at
    BEFORE UPDATE ON images
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_collections_updated_at ON collections;
CREATE TRIGGER update_collections_updated_at
    BEFORE UPDATE ON collections
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Vector similarity search function
CREATE OR REPLACE FUNCTION match_images(
    query_embedding vector(512),
    match_threshold float DEFAULT 0.0,
    match_count int DEFAULT 10
)
RETURNS TABLE (
    id uuid,
    image_url text,
    source_platform text,
    source_collection text,
    similarity float
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        images.id,
        images.image_url,
        images.source_platform,
        images.source_collection,
        1 - (images.embedding <=> query_embedding)::float as similarity
    FROM images
    WHERE images.embedding IS NOT NULL
      AND (1 - (images.embedding <=> query_embedding)) >= match_threshold
    ORDER BY images.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;

-- Migration: add new analysis fields (run if table already exists)
ALTER TABLE collections ADD COLUMN IF NOT EXISTS recurring_forms JSONB DEFAULT '[]';
ALTER TABLE collections ADD COLUMN IF NOT EXISTS color_palette JSONB DEFAULT '[]';
ALTER TABLE collections ADD COLUMN IF NOT EXISTS mood_atmosphere TEXT;
ALTER TABLE collections ADD COLUMN IF NOT EXISTS installation_directions JSONB DEFAULT '[]';
