import torch
import torch.nn as nn
import matplotlib.pyplot as plt
import torch.nn.functional as F
import seaborn as sns
import numpy as np
from torch_sparse import spspmm
from sklearn.metrics.pairwise import cosine_similarity
import networkx as nx


# import modules
from src.visualization.visualize import Visualization
from src.features.link_prediction import Link_prediction_counts
from src.features.preprocessing import Preprocessing

class DRRAA_counts(nn.Module, Preprocessing, Link_prediction_counts, Visualization):
    def __init__(self, k, d, sample_size, data, counts, true_counts, data_type = "edge list", data_2 = None, link_pred=False, test_size=0.3, non_sparse_i = None, non_sparse_j = None, sparse_i_rem = None, sparse_j_rem = None, seed_split = False, seed_init = False, init_Z = None, graph = False):
        # TODO Skal finde en måde at loade data ind på. CHECK
        # TODO Skal sørge for at alle classes får de parametre de skal bruge. CHECK
        # TODO Skal ha indført en train funktion/class. CHECK
        # TODO SKal ha udvides visualiseringskoden. CHECK
        # TODO Skal ha lavet performance check ()
        # TODO Skal vi lave en sampling_weights med andet end 1 taller?

        super(DRRAA_counts, self).__init__()
        self.data_type = data_type
        self.link_pred = link_pred
        self.counts = counts
        self.true_counts = true_counts
        if link_pred:
            self.test_size = test_size
        self.device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
  
        if self.data_type != "sparse":
            Preprocessing.__init__(self, data = data, data_type = data_type, device = self.device, data_2 = data_2)
            self.edge_list, self.N, self.G = Preprocessing.convert_to_egde_list(self)
            if link_pred:
                if seed_split != False:
                    np.random.seed(seed_split)
                    torch.manual_seed(seed_split)
                Link_prediction_counts.__init__(self)
            if seed_init != False:
                np.random.seed(seed_init)
                torch.manual_seed(seed_init)
            self.sparse_i_idx = self.edge_list[0]
            self.sparse_i_idx = self.sparse_i_idx.to(self.device)
            self.sparse_j_idx = self.edge_list[1]
            self.sparse_j_idx = self.sparse_j_idx.to(self.device)


        if self.data_type == "sparse":
            #create indices to index properly the receiver and senders variable
            if link_pred:
                Link_prediction_counts.__init__(self)
            self.sparse_i_idx = data.to(self.device)
            self.sparse_j_idx = data_2.to(self.device)
            self.non_sparse_i_idx_removed = non_sparse_i.to(self.device)
            self.non_sparse_j_idx_removed = non_sparse_j.to(self.device)
            self.sparse_i_idx_removed = sparse_i_rem.to(self.device)
            self.sparse_j_idx_removed = sparse_j_rem.to(self.device)
            self.removed_i = torch.cat((self.non_sparse_i_idx_removed, self.sparse_i_idx_removed))
            self.removed_j = torch.cat((self.non_sparse_j_idx_removed, self.sparse_j_idx_removed))
            self.N = int(self.sparse_j_idx.max() + 1)

        
        Visualization.__init__(self)

        self.input_size = (self.N, self.N)
        self.k = k
        self.d = d

        self.beta = torch.nn.Parameter(torch.randn(self.input_size[0], device = self.device))
        self.softplus = nn.Softplus()
        self.A = torch.nn.Parameter(torch.randn(self.d, self.k, device = self.device))
        if init_Z != None:
            self.Z = torch.nn.Parameter(init_Z).to(self.device)
        else:    
            self.Z = torch.nn.Parameter(torch.randn(self.k, self.input_size[0], device = self.device))

        self.Gate = torch.nn.Parameter(torch.randn(self.input_size[0], self.k, device = self.device))

        self.missing_data = False
        self.sampling_weights = torch.ones(self.N, device = self.device)
        self.sample_size = round(sample_size * self.N) #TODO check if this need changing

        # list for training loss 
        self.losses = []
        self.test_losses = []


    def sample_network(self):
        # USE torch_sparse lib i.e. : from torch_sparse import spspmm

        # sample for undirected network
        sample_idx = torch.multinomial(self.sampling_weights, self.sample_size, replacement=False)
        # translate sampled indices w.r.t. to the full matrix, it is just a diagonal matrix
        indices_translator = torch.cat([sample_idx.unsqueeze(0), sample_idx.unsqueeze(0)], 0)
        # adjacency matrix in edges format
        edges = torch.cat([self.sparse_i_idx.unsqueeze(0), self.sparse_j_idx.unsqueeze(0)], 0) #.to(self.device)
        # matrix multiplication B = Adjacency x Indices translator
        # see spspmm function, it give a multiplication between two matrices
        # indexC is the indices where we have non-zero values and valueC the actual values
        indexC, valueC = spspmm(edges, self.counts.float(), indices_translator,
                                torch.ones(indices_translator.shape[1], device = self.device), self.input_size[0], self.input_size[0],
                                self.input_size[0], coalesced=True)

        # second matrix multiplication C = Indices translator x B, indexC returns where we have edges inside the sample
        indexC, valueC = spspmm(indices_translator, torch.ones(indices_translator.shape[1], device = self.device), indexC, valueC,
                                self.input_size[0], self.input_size[0], self.input_size[0], coalesced=True)

        # edge row position
        sparse_i_sample = indexC[0, :]
        # edge column position
        sparse_j_sample = indexC[1, :]

        return sample_idx, sparse_i_sample, sparse_j_sample, valueC

    def log_likelihood(self):
        sample_idx, sparse_sample_i, sparse_sample_j, valueC = self.sample_network()
        Z = F.softmax(self.Z, dim=0) #(K x N)
        G = torch.sigmoid(self.Gate) #Sigmoid activation function
        C = (Z.T * G) / (Z.T * G).sum(0) #Gating function
        #For the nodes without links
        beta = self.beta[sample_idx].unsqueeze(1) + self.beta[sample_idx] #(N x N)
        AZCz = torch.mm(self.A, torch.mm(torch.mm(Z[:,sample_idx], C[sample_idx,:]), Z[:,sample_idx])).T
        mat = torch.exp(beta-((AZCz.unsqueeze(1) - AZCz + 1e-06) ** 2).sum(-1) ** 0.5)
        z_pdist1 = (0.5 * (mat - torch.diag(torch.diagonal(mat))).sum())

        #For the nodes with links
        AZC = torch.mm(self.A, torch.mm(Z[:, sample_idx],C[sample_idx, :])) #This could perhaps be a computational issue
        z_pdist2 = (valueC * (self.beta[sparse_sample_i] + self.beta[sparse_sample_j] - (((( torch.matmul(AZC, Z[:, sparse_sample_i]).T - torch.mm(AZC, Z[:, sparse_sample_j]).T + 1e-06) ** 2).sum(-1))) ** 0.5)).sum()

        log_likelihood_sparse = z_pdist2 - z_pdist1
        return log_likelihood_sparse

    def test_log_likelihood(self):
        with torch.no_grad():
            Z = torch.softmax(self.Z, dim=0)
            G = torch.sigmoid(self.Gate)
            C = (Z.T * G) / (Z.T * G).sum(0)  # Gating function

            M_i = torch.matmul(self.A, torch.matmul(torch.matmul(Z, C),
                                                    Z[:, self.idx_i_test])).T  # Size of test set e.g. K x N
            M_j = torch.matmul(self.A, torch.matmul(torch.matmul(Z, C), Z[:, self.idx_j_test])).T
            z_pdist_test = ((M_i - M_j + 1e-06) ** 2).sum(-1) ** 0.5  # N x N
            Lambda = (self.beta[self.idx_i_test] + self.beta[self.idx_j_test] - z_pdist_test)  # (test_size)
            
            return (self.true_counts[self.idx_i_test, self.idx_j_test] * Lambda).sum() - torch.exp(Lambda).sum()
    

    def train(self, iterations, LR = 0.01, early_stopping=None, print_loss = False, scheduling = False):
        optimizer = torch.optim.Adam(params = self.parameters(), lr=LR)
        scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
            optimizer,
            mode='max',
            factor=0.9,
            patience=10,
            verbose=True
        )
        if not scheduling:
            for _ in range(iterations):
                 #self.sample_size #self.N
                if self.link_pred:
                    loss = - self.log_likelihood() / ((1-self.test_size)*(0.5*(self.N*(self.N-1)))) 
                    loss_test = - self.test_log_likelihood() / (self.test_size*(0.5*(self.N*(self.N-1)))) #(self.test_size * self.sample_size)
                    self.test_losses.append(loss_test.item())
                else:
                    loss = - self.log_likelihood() / self.sample_size
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()
                self.losses.append(loss.item())
                if print_loss:
                    print('Loss at the',_,'iteration:',loss.item())
        else:
            for iter in range(iterations):
                if iter == 0:
                    Z = torch.softmax(self.Z, dim=0)
                    G = torch.sigmoid(self.Gate)
                    C = (Z.T * G) / (Z.T * G).sum(0)
                    u, sigma, v = torch.svd(self.A) # Decomposition of A.
                    r = torch.matmul(torch.diag(sigma), v.T)
                    last_embeddings = torch.matmul(r, torch.matmul(torch.matmul(Z, C), Z)).T
                    last_embeddings = last_embeddings.cpu().detach().numpy()
                    last_archetypes =  torch.matmul(r, torch.matmul(Z, C))
                    last_archetypes = last_archetypes.cpu().detach().numpy()
                loss = - self.log_likelihood() / self.sample_size #self.N
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()
                self.losses.append(loss.item())
                if print_loss:
                    print('Loss at the',iter,'iteration:',loss.item())
                if iter % 1000 == 0 and iter != 0:
                    print('Loss at the',iter,'iteration:',loss.item())
                    # Learning rate scheduler based on cosine similarity
                    Z = torch.softmax(self.Z, dim=0)
                    G = torch.sigmoid(self.Gate)
                    C = (Z.T * G) / (Z.T * G).sum(0)
                    u, sigma, v = torch.svd(self.A) # Decomposition of A.
                    r = torch.matmul(torch.diag(sigma), v.T)
                    embeddings = torch.matmul(r, torch.matmul(torch.matmul(Z, C), Z)).T
                    archetypes = torch.matmul(r, torch.matmul(Z, C))
                    embeddings = embeddings.cpu().detach().numpy()
                    archetypes = archetypes.cpu().detach().numpy()
                    cosine_matrix = cosine_similarity(last_embeddings, embeddings)
                    cosine_between_iter = np.diagonal(cosine_matrix)
                    cosine_matrix2 = cosine_similarity(last_archetypes, archetypes)
                    cosine_between_iter_2 = np.diagonal(cosine_matrix2)
                    mu_cosine_similarity = (np.mean(cosine_between_iter) + np.mean(cosine_between_iter_2)) /2
                    scheduler.step(mu_cosine_similarity)
                    last_embeddings = embeddings
                    print(mu_cosine_similarity)
                    #if early_stopping != None:
                    #    if mu_cosine_similarity > early_stopping:
                    #        print(f"Early stopping occured given that the model has found a stable latent space at {iter} iterations")
                    #        break


        