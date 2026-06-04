classdef MockRavenProject < handle
    properties
        room_material_names
        shoebox_dims
        material_calls
        source_names
        source_positions
        source_view_vectors
        source_up_vectors
        directivity_calls
    end

    methods
        function obj = MockRavenProject(room_material_names)
            if nargin < 1
                room_material_names = {};
            end

            obj.room_material_names = room_material_names;
            obj.shoebox_dims = [];
            obj.material_calls = struct('slot_name', {}, 'absorp', {}, 'scatter', {});
            obj.source_names = {};
            obj.source_positions = [];
            obj.source_view_vectors = [];
            obj.source_up_vectors = [];
            obj.directivity_calls = {};
        end

        function setModelToShoebox(obj, x, y, z)
            obj.shoebox_dims = [x y z];
        end

        function names = getRoomMaterialNames(obj)
            names = obj.room_material_names;
        end

        function setMaterial(obj, slot_name, absorp, scatter)
            obj.material_calls(end + 1) = struct( ...
                'slot_name', slot_name, ...
                'absorp', absorp, ...
                'scatter', scatter);
        end

        function setSourceNames(obj, source_names)
            obj.source_names = source_names;
        end

        function setSourcePositions(obj, source_positions)
            obj.source_positions = source_positions;
        end

        function setSourceViewVectors(obj, source_view_vectors)
            obj.source_view_vectors = source_view_vectors;
        end

        function setSourceUpVectors(obj, source_up_vectors)
            obj.source_up_vectors = source_up_vectors;
        end

        function setSourceDirectivity(obj, directivity_path)
            obj.directivity_calls{end + 1} = char(string(directivity_path));
        end
    end
end
